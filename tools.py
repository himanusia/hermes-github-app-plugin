"""Hermes tool schemas and handlers for the standalone GitHub connector."""

from __future__ import annotations

import json
from typing import Any, Callable

from .client import GitHubClient, GitHubError, parse_repo
from .config import setting_bool


Json = dict[str, Any]


def _ok(payload: Json) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _error(code: str, message: str, *, status_code: int | None = None) -> str:
    payload: Json = {"success": False, "error": {"code": code, "message": message}}
    if status_code is not None:
        payload["error"]["status_code"] = status_code
    return _ok(payload)


def _client_error(exc: Exception) -> str:
    if isinstance(exc, GitHubError):
        return _error(exc.code, exc.message, status_code=exc.status_code)
    return _error("internal_error", f"GitHub connector failed: {type(exc).__name__}")


def _repo(args: Json) -> tuple[str, str]:
    return parse_repo(str(args.get("repo", "")))


def _number(args: Json) -> int:
    try:
        number = int(args.get("number", 0))
    except (TypeError, ValueError) as exc:
        raise GitHubError("number must be a positive integer", code="invalid_argument") from exc
    if number <= 0:
        raise GitHubError("number must be a positive integer", code="invalid_argument")
    return number


def _write_enabled(ctx: object) -> bool:
    return setting_bool(ctx, "allow_write_actions", False)


def _write_guard(ctx: object) -> str | None:
    if not _write_enabled(ctx):
        return _error(
            "write_actions_disabled",
            "GitHub write actions are disabled. Set plugins.entries.github_app.settings.allow_write_actions=true to enable them.",
        )
    return None


def build_handlers(
    ctx: object,
    *,
    client_factory: Callable[[], GitHubClient] = GitHubClient,
) -> dict[str, Callable[..., str]]:
    """Build handlers bound to the operator's plugin settings."""

    def identity(args: Json, **kwargs: Any) -> str:
        try:
            return _ok({"success": True, "action": "identity", **client_factory().verify_identity()})
        except Exception as exc:
            return _client_error(exc)

    def create_issue(args: Json, **kwargs: Any) -> str:
        if (blocked := _write_guard(ctx)) is not None:
            return blocked
        try:
            owner, repo = _repo(args)
            client = client_factory()
            issue = client.create_issue(
                owner,
                repo,
                str(args.get("title", "")),
                str(args.get("body", "") or ""),
                args.get("labels"),
                args.get("assignees"),
            )
            return _ok({
                "success": True,
                "action": "create_issue",
                "issue_number": issue.get("number"),
                "url": issue.get("html_url"),
                "state": issue.get("state"),
                "title": issue.get("title"),
                "attribution": client.attribution(),
            })
        except Exception as exc:
            return _client_error(exc)

    def comment_issue(args: Json, **kwargs: Any) -> str:
        if (blocked := _write_guard(ctx)) is not None:
            return blocked
        try:
            owner, repo = _repo(args)
            client = client_factory()
            comment = client.comment_issue(owner, repo, _number(args), str(args.get("body", "")))
            return _ok({
                "success": True,
                "action": "comment_issue",
                "comment_id": comment.get("id"),
                "url": comment.get("html_url"),
                "author": (comment.get("user") or {}).get("login"),
                "attribution": client.attribution(),
            })
        except Exception as exc:
            return _client_error(exc)

    def list_issues(args: Json, **kwargs: Any) -> str:
        try:
            owner, repo = _repo(args)
            client = client_factory()
            issues = client.list_issues(
                owner,
                repo,
                state=str(args.get("state", "open")),
                labels=str(args.get("labels", "") or ""),
                assignee=str(args.get("assignee", "") or ""),
                creator=str(args.get("creator", "") or ""),
                sort=str(args.get("sort", "created")),
                direction=str(args.get("direction", "desc")),
                per_page=int(args.get("per_page") or 30),
            )
            summary = [
                {
                    "number": issue.get("number"),
                    "title": issue.get("title"),
                    "state": issue.get("state"),
                    "user": (issue.get("user") or {}).get("login"),
                    "labels": [label.get("name") for label in (issue.get("labels") or []) if isinstance(label, dict)],
                    "created_at": issue.get("created_at"),
                    "updated_at": issue.get("updated_at"),
                    "pull_request": "pull_request" in issue,
                }
                for issue in issues
            ]
            return _ok({
                "success": True,
                "action": "list_issues",
                "count": len(summary),
                "issues": summary,
                "attribution": client.attribution(),
            })
        except Exception as exc:
            return _client_error(exc)

    def get_issue(args: Json, **kwargs: Any) -> str:
        try:
            owner, repo = _repo(args)
            client = client_factory()
            issue = client.get_issue(owner, repo, _number(args), include_comments=bool(args.get("include_comments")))
            return _ok({
                "success": True,
                "action": "get_issue",
                "issue": issue,
                "attribution": client.attribution(),
            })
        except Exception as exc:
            return _client_error(exc)

    def review_pr(args: Json, **kwargs: Any) -> str:
        if (blocked := _write_guard(ctx)) is not None:
            return blocked
        try:
            owner, repo = _repo(args)
            client = client_factory()
            review = client.review_pull_request(
                owner,
                repo,
                _number(args),
                str(args.get("event", "")),
                str(args.get("body", "") or ""),
                args.get("comments"),
            )
            return _ok({
                "success": True,
                "action": "review_pr",
                "review_id": review.get("id"),
                "state": review.get("state"),
                "url": review.get("html_url"),
                "attribution": client.attribution(),
            })
        except Exception as exc:
            return _client_error(exc)

    def merge_pr(args: Json, **kwargs: Any) -> str:
        if (blocked := _write_guard(ctx)) is not None:
            return blocked
        try:
            owner, repo = _repo(args)
            client = client_factory()
            result = client.merge_pull_request(
                owner,
                repo,
                _number(args),
                method=str(args.get("method") or "squash"),
                commit_title=str(args.get("commit_title", "") or ""),
            )
            return _ok({
                "success": bool(result.get("merged")),
                "action": "merge_pr",
                "merged": result.get("merged"),
                "message": result.get("message"),
                "sha": result.get("sha"),
                "url": result.get("html_url"),
                "attribution": client.attribution(),
            })
        except Exception as exc:
            return _client_error(exc)

    return {
        "github_identity": identity,
        "github_create_issue": create_issue,
        "github_comment_issue": comment_issue,
        "github_list_issues": list_issues,
        "github_get_issue": get_issue,
        "github_review_pr": review_pr,
        "github_merge_pr": merge_pr,
    }


GITHUB_IDENTITY_SCHEMA = {
    "name": "github_identity",
    "description": "Verify the GitHub identity Hermes uses. Prefer this before any write action; the result identifies the bot or human actor and accessible repositories.",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

GITHUB_CREATE_ISSUE_SCHEMA = {
    "name": "github_create_issue",
    "description": "Create an issue as the resolved GitHub identity. Requires the operator's explicit allow_write_actions plugin setting and returns bot/human attribution.",
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Explicit owner/name repository, for example himanusia/hermes-agent."},
            "title": {"type": "string", "description": "Issue title."},
            "body": {"type": "string", "description": "Markdown issue body."},
            "labels": {"type": "array", "items": {"type": "string"}, "description": "Optional label names."},
            "assignees": {"type": "array", "items": {"type": "string"}, "description": "Optional GitHub logins."},
        },
        "required": ["repo", "title"],
    },
}

GITHUB_COMMENT_ISSUE_SCHEMA = {
    "name": "github_comment_issue",
    "description": "Comment on an issue or pull request as the resolved GitHub identity. Requires the operator's explicit allow_write_actions plugin setting and returns attribution.",
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Explicit owner/name repository."},
            "number": {"type": "integer", "description": "Issue or pull request number."},
            "body": {"type": "string", "description": "Markdown comment body."},
        },
        "required": ["repo", "number", "body"],
    },
}

GITHUB_LIST_ISSUES_SCHEMA = {
    "name": "github_list_issues",
    "description": "List issues and pull requests in a repository. Read-only; returns a compact result with actor attribution.",
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Explicit owner/name repository."},
            "state": {"type": "string", "enum": ["open", "closed", "all"], "description": "Issue state."},
            "labels": {"type": "string", "description": "Comma-separated labels."},
            "assignee": {"type": "string", "description": "Assignee login."},
            "creator": {"type": "string", "description": "Creator login."},
            "sort": {"type": "string", "enum": ["created", "updated", "comments"], "description": "Sort field."},
            "direction": {"type": "string", "enum": ["asc", "desc"], "description": "Sort direction."},
            "per_page": {"type": "integer", "description": "1-100 results."},
        },
        "required": ["repo"],
    },
}

GITHUB_GET_ISSUE_SCHEMA = {
    "name": "github_get_issue",
    "description": "Fetch an issue or pull request and optionally its comments. Read-only; returns actor attribution.",
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Explicit owner/name repository."},
            "number": {"type": "integer", "description": "Issue or pull request number."},
            "include_comments": {"type": "boolean", "description": "Whether to fetch the comment thread."},
        },
        "required": ["repo", "number"],
    },
}

GITHUB_REVIEW_PR_SCHEMA = {
    "name": "github_review_pr",
    "description": "Submit an APPROVE, REQUEST_CHANGES, or COMMENT review as the resolved identity. Requires explicit allow_write_actions and returns attribution.",
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Explicit owner/name repository."},
            "number": {"type": "integer", "description": "Pull request number."},
            "event": {"type": "string", "enum": ["APPROVE", "REQUEST_CHANGES", "COMMENT"], "description": "Review event."},
            "body": {"type": "string", "description": "Markdown review body."},
            "comments": {
                "type": "array",
                "description": "Optional inline comments. Each needs path and body; line, side, and commit_id are optional.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "line": {"type": "integer"},
                        "side": {"type": "string", "enum": ["LEFT", "RIGHT"]},
                        "body": {"type": "string"},
                        "commit_id": {"type": "string"},
                    },
                    "required": ["path", "body"],
                },
            },
        },
        "required": ["repo", "number", "event"],
    },
}

GITHUB_MERGE_PR_SCHEMA = {
    "name": "github_merge_pr",
    "description": "Merge a pull request as the resolved identity. Requires explicit allow_write_actions and separate operator authorization for the particular merge; default method is squash.",
    "parameters": {
        "type": "object",
        "properties": {
            "repo": {"type": "string", "description": "Explicit owner/name repository."},
            "number": {"type": "integer", "description": "Pull request number."},
            "method": {"type": "string", "enum": ["squash", "merge", "rebase"], "description": "Merge method."},
            "commit_title": {"type": "string", "description": "Optional merge commit title."},
        },
        "required": ["repo", "number"],
    },
}

TOOL_SPECS = (
    ("github_identity", GITHUB_IDENTITY_SCHEMA, "🆔"),
    ("github_create_issue", GITHUB_CREATE_ISSUE_SCHEMA, "🐛"),
    ("github_comment_issue", GITHUB_COMMENT_ISSUE_SCHEMA, "💬"),
    ("github_list_issues", GITHUB_LIST_ISSUES_SCHEMA, "📋"),
    ("github_get_issue", GITHUB_GET_ISSUE_SCHEMA, "🔍"),
    ("github_review_pr", GITHUB_REVIEW_PR_SCHEMA, "👁️"),
    ("github_merge_pr", GITHUB_MERGE_PR_SCHEMA, "🔀"),
)

__all__ = ["TOOL_SPECS", "build_handlers"]
