"""Dependency-light GitHub REST client for the external connector.

Only the standard library is used for HTTP. GitHub App signing is delegated to
PyJWT in ``auth.py``; no Hermes private module is imported here.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

from .auth import GitHubAuthError, app_credentials_configured, build_app_jwt, gh_cli_token
from .config import load_github_env

API_BASE = "https://api.github.com"
_DEFAULT_TIMEOUT = 15.0
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

Transport = Callable[..., tuple[int, Mapping[str, str], Any]]


class GitHubError(RuntimeError):
    """A safe GitHub connector error with an optional HTTP status."""

    def __init__(self, message: str, status_code: int | None = None, code: str = "github_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def _decode_body(raw: bytes) -> Any:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except (TypeError, ValueError):
        return {"_raw": raw.decode("utf-8", errors="replace")[:2000]}


def _default_transport(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    params: Mapping[str, Any] | None = None,
    payload: Any = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[int, Mapping[str, str], Any]:
    if params:
        query = urllib.parse.urlencode(
            [(key, value) for key, value in params.items() if value is not None],
            doseq=True,
        )
        if query:
            url = f"{url}{'&' if '?' in url else '?'}{query}"
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = dict(headers)
    if data is not None:
        request_headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, dict(response.headers.items()), _decode_body(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers.items()) if exc.headers else {}, _decode_body(exc.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GitHubError("GitHub API connection failed", code="connection_error") from exc


def parse_repo(value: str) -> tuple[str, str]:
    """Validate and split an explicit ``owner/name`` repository reference."""
    raw = str(value or "").strip().strip("/")
    parts = raw.split("/")
    if len(parts) != 2 or not all(parts) or not all(_NAME_RE.fullmatch(part) for part in parts):
        raise GitHubError("repo must use the explicit owner/name format", code="invalid_repo")
    return parts[0], parts[1]


def _positive_int(value: Any, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise GitHubError(f"{field} must be a positive integer", code="invalid_argument") from exc
    if number <= 0:
        raise GitHubError(f"{field} must be a positive integer", code="invalid_argument")
    return number


class GitHubClient:
    """Small REST client with bot-first credential resolution."""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        transport: Transport | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.env = dict(env or load_github_env())
        self.transport = transport or _default_transport
        self.timeout = float(timeout)
        self._token = ""
        self._method = ""
        self._actor = ""
        self._app_jwt = ""

    def _build_app_jwt(self) -> str:
        try:
            return build_app_jwt(self.env)
        except GitHubAuthError as exc:
            raise GitHubError(str(exc), code="auth_error") from exc

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        token: str = "",
        params: Mapping[str, Any] | None = None,
        payload: Any = None,
    ) -> Any:
        if not path.startswith("/"):
            raise GitHubError("invalid GitHub API path", code="invalid_path")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "hermes-github-app-plugin/0.1.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        status, _, body = self.transport(
            method.upper(),
            f"{API_BASE}{path}",
            headers=headers,
            params=params,
            payload=payload,
            timeout=self.timeout,
        )
        if status >= 400:
            message = body.get("message") if isinstance(body, dict) else None
            raise GitHubError(
                str(message or f"GitHub API returned HTTP {status}"),
                status_code=int(status),
                code="http_error",
            )
        return body

    def _app_slug(self, app_jwt: str) -> str:
        configured = self.env.get("GITHUB_APP_SLUG", "").strip()
        if configured:
            return configured
        try:
            data = self._raw_request("GET", "/app", token=app_jwt)
            slug = data.get("slug") if isinstance(data, dict) else ""
            if isinstance(slug, str) and slug.strip():
                return slug.strip()
        except GitHubError:
            pass
        return "github-app"

    def _clear_credentials(self) -> None:
        self._token = ""
        self._method = ""
        self._actor = ""
        self._app_jwt = ""

    def _resolve_credentials(self) -> tuple[str, str]:
        if self._token:
            return self._method, self._token

        if app_credentials_configured(self.env):
            try:
                self._app_jwt = self._build_app_jwt()
                installation = self.env.get("GITHUB_APP_INSTALLATION_ID", "").strip()
                if not installation.isdigit():
                    raise GitHubAuthError("GITHUB_APP_INSTALLATION_ID must be numeric")
                response = self._raw_request(
                    "POST",
                    f"/app/installations/{installation}/access_tokens",
                    token=self._app_jwt,
                    payload={},
                )
                token = response.get("token") if isinstance(response, dict) else ""
                if not isinstance(token, str) or not token.strip():
                    raise GitHubAuthError("GitHub App installation token was not returned")
                self._token = token.strip()
                self._method = "github-app"
                self._actor = f"{self._app_slug(self._app_jwt)}[bot]"
                return self._method, self._token
            except GitHubAuthError:
                raise
            except GitHubError as exc:
                raise GitHubAuthError("GitHub App installation token request failed") from exc

        token = self.env.get("GITHUB_TOKEN", "").strip() or self.env.get("GH_TOKEN", "").strip()
        method = "pat"
        if not token:
            token = gh_cli_token()
            method = "gh-cli"
        if token:
            self._token = token
            self._method = method
            return self._method, self._token
        raise GitHubError(
            "No GitHub credentials configured. Configure the GitHub App variables, "
            "GITHUB_TOKEN/GH_TOKEN, or an authenticated gh CLI.",
            code="missing_credentials",
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        payload: Any = None,
        retried: bool = False,
    ) -> Any:
        _, token = self._resolve_credentials()
        try:
            return self._raw_request(method, path, token=token, params=params, payload=payload)
        except GitHubError as exc:
            if exc.status_code == 401 and not retried:
                self._clear_credentials()
                return self._request(method, path, params=params, payload=payload, retried=True)
            raise

    @property
    def auth_method(self) -> str:
        return self._method or "unknown"

    @property
    def actor(self) -> str:
        if self._actor:
            return self._actor
        if self._method in {"pat", "gh-cli"}:
            try:
                user = self._request("GET", "/user")
                login = user.get("login") if isinstance(user, dict) else ""
                self._actor = str(login or "unknown-user")
            except GitHubError:
                self._actor = "unknown-user"
        return self._actor or "unknown"

    def attribution(self) -> dict[str, str]:
        self._resolve_credentials()
        return {"auth_method": self.auth_method, "actor": self.actor}

    def close(self) -> None:
        """Release in-memory token state; urllib has no persistent session."""
        self._token = ""
        self._actor = ""

    def verify_identity(self) -> dict[str, Any]:
        method, _ = self._resolve_credentials()
        # App resolution already knows its actor. For a PAT/gh token, defer the
        # single /user request below instead of asking the API twice.
        info: dict[str, Any] = {
            "auth_method": method,
            "actor": self._actor or ("unknown" if method != "github-app" else "github-app[bot]"),
        }
        if method == "github-app":
            try:
                repos = self._request("GET", "/installation/repositories", params={"per_page": 100})
                raw_repos = repos.get("repositories", []) if isinstance(repos, dict) else []
                info["accessible_repos"] = [r.get("full_name") for r in raw_repos if isinstance(r, dict)]
            except GitHubError as exc:
                info["accessible_repos_error"] = exc.message
            info["app_slug"] = self.actor.removesuffix("[bot]")
            return info

        user = self._request("GET", "/user")
        if isinstance(user, dict):
            self._actor = str(user.get("login") or "unknown-user")
            info["actor"] = self._actor
            info["account"] = self._actor
            info["user_type"] = user.get("type")
        repos = self._request("GET", "/user/repos", params={"per_page": 100, "sort": "updated"})
        info["accessible_repos"] = [r.get("full_name") for r in repos if isinstance(r, dict)] if isinstance(repos, list) else []
        return info

    def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> dict[str, Any]:
        if not str(title or "").strip():
            raise GitHubError("title is required", code="invalid_argument")
        payload: dict[str, Any] = {"title": str(title).strip()}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees
        result = self._request("POST", f"/repos/{owner}/{repo}/issues", payload=payload)
        return result if isinstance(result, dict) else {}

    def comment_issue(self, owner: str, repo: str, number: int, body: str) -> dict[str, Any]:
        number = _positive_int(number, "number")
        if not str(body or "").strip():
            raise GitHubError("body is required", code="invalid_argument")
        result = self._request(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/comments", payload={"body": body}
        )
        return result if isinstance(result, dict) else {}

    def list_issues(
        self,
        owner: str,
        repo: str,
        *,
        state: str = "open",
        labels: str = "",
        assignee: str = "",
        creator: str = "",
        sort: str = "created",
        direction: str = "desc",
        per_page: int = 30,
    ) -> list[dict[str, Any]]:
        state = state if state in {"open", "closed", "all"} else "open"
        sort = sort if sort in {"created", "updated", "comments"} else "created"
        direction = direction if direction in {"asc", "desc"} else "desc"
        params: dict[str, Any] = {
            "state": state,
            "sort": sort,
            "direction": direction,
            "per_page": min(max(int(per_page), 1), 100),
        }
        for key, value in (("labels", labels), ("assignee", assignee), ("creator", creator)):
            if value:
                params[key] = value
        result = self._request("GET", f"/repos/{owner}/{repo}/issues", params=params)
        return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else []

    def get_issue(self, owner: str, repo: str, number: int, *, include_comments: bool = False) -> dict[str, Any]:
        number = _positive_int(number, "number")
        issue = self._request("GET", f"/repos/{owner}/{repo}/issues/{number}")
        result = issue if isinstance(issue, dict) else {}
        if include_comments:
            comments = self._request(
                "GET", f"/repos/{owner}/{repo}/issues/{number}/comments", params={"per_page": 100}
            )
            result["comments_data"] = comments if isinstance(comments, list) else []
        return result

    def get_pull_request(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        number = _positive_int(number, "number")
        result = self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}")
        return result if isinstance(result, dict) else {}

    def review_pull_request(
        self,
        owner: str,
        repo: str,
        number: int,
        event: str,
        body: str = "",
        comments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        number = _positive_int(number, "number")
        event = str(event or "").upper()
        if event not in {"APPROVE", "REQUEST_CHANGES", "COMMENT"}:
            raise GitHubError("event must be APPROVE, REQUEST_CHANGES, or COMMENT", code="invalid_argument")
        payload: dict[str, Any] = {"event": event}
        if body:
            payload["body"] = body
        if comments:
            normalized: list[dict[str, Any]] = []
            for comment in comments:
                if not isinstance(comment, dict) or not comment.get("path") or not comment.get("body"):
                    raise GitHubError("each inline comment needs path and body", code="invalid_argument")
                item = {"path": comment["path"], "body": comment["body"], "side": comment.get("side", "RIGHT")}
                if comment.get("line") is not None:
                    item["line"] = _positive_int(comment["line"], "comments.line")
                if comment.get("commit_id"):
                    item["commit_id"] = comment["commit_id"]
                normalized.append(item)
            payload["comments"] = normalized
        result = self._request("POST", f"/repos/{owner}/{repo}/pulls/{number}/reviews", payload=payload)
        return result if isinstance(result, dict) else {}

    def merge_pull_request(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        method: str = "squash",
        commit_title: str = "",
    ) -> dict[str, Any]:
        number = _positive_int(number, "number")
        method = str(method or "squash").lower()
        if method not in {"squash", "merge", "rebase"}:
            raise GitHubError("method must be squash, merge, or rebase", code="invalid_argument")
        payload: dict[str, Any] = {"merge_method": method}
        if commit_title:
            payload["commit_title"] = commit_title
        result = self._request("PUT", f"/repos/{owner}/{repo}/pulls/{number}/merge", payload=payload)
        return result if isinstance(result, dict) else {}


def github_credentials_available() -> bool:
    """Cheap, side-effect-light tool gate; does not call GitHub."""
    env = load_github_env()
    return bool(
        app_credentials_configured(env)
        or env.get("GITHUB_TOKEN", "").strip()
        or env.get("GH_TOKEN", "").strip()
        or gh_cli_token()
    )


__all__ = ["GitHubClient", "GitHubError", "github_credentials_available", "parse_repo"]
