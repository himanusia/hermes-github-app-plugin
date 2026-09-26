"""Standalone GitHub App JWT and credential helpers."""

from __future__ import annotations

import os
import subprocess
import time
import urllib.parse
from pathlib import Path
from typing import Mapping

from .config import hermes_home

API_BASE = "https://api.github.com"
_API_BASE_ENV = "GITHUB_API_URL"


class GitHubAuthError(RuntimeError):
    """A local credential/configuration error without secret-bearing details."""


def validate_api_base(raw: str | None = None) -> str:
    """Validate and normalize the REST API base before sending credentials."""
    value = str(raw or API_BASE).strip()
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise GitHubAuthError("GITHUB_API_URL is not a valid HTTPS URL") from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or "\\" in parsed.netloc
        or "%" in parsed.netloc
        or any(char.isspace() for char in value)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in value
        or "#" in value
    ):
        raise GitHubAuthError(
            "GITHUB_API_URL must be an HTTPS API base URL without credentials, query, or fragment"
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise GitHubAuthError("GITHUB_API_URL contains an invalid port") from exc
    if any(ord(char) < 0x20 or ord(char) == 0x7f for char in value):
        raise GitHubAuthError("GITHUB_API_URL contains invalid control characters")
    return urllib.parse.urlunsplit(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", "")).rstrip("/")


def _hostname_for_api(api_base: str) -> str:
    parsed = urllib.parse.urlsplit(api_base)
    host = parsed.hostname or "api.github.com"
    if host.lower() == "api.github.com":
        return "github.com"
    # Preserve GHES' non-default port in the host label recognized by gh.
    if parsed.port is not None:
        host = f"[{host}]" if ":" in host else host
        return f"{host}:{parsed.port}"
    return f"[{host}]" if ":" in host else host


def _private_key(env: Mapping[str, str]) -> str:
    raw = env.get("GITHUB_APP_PRIVATE_KEY", "")
    if raw:
        return raw.replace("\\n", "\n")
    raw_path = env.get("GITHUB_APP_PRIVATE_KEY_PATH") or env.get("GITHUB_APP_PRIVATE_KEY_FILE", "")
    if not raw_path:
        return ""
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = hermes_home() / path
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GitHubAuthError("GitHub App private key file is unreadable") from exc


def app_credentials_attempted(env: Mapping[str, str]) -> bool:
    """Whether the operator supplied any required App-auth field (even an incomplete set)."""
    return any(
        env.get(key, "").strip()
        for key in (
            "GITHUB_APP_ID",
            "GITHUB_APP_INSTALLATION_ID",
            "GITHUB_APP_PRIVATE_KEY",
            "GITHUB_APP_PRIVATE_KEY_PATH",
            "GITHUB_APP_PRIVATE_KEY_FILE",
        )
    )


def app_credentials_configured(env: Mapping[str, str]) -> bool:
    return bool(
        env.get("GITHUB_APP_ID", "").strip()
        and env.get("GITHUB_APP_INSTALLATION_ID", "").strip()
        and (env.get("GITHUB_APP_PRIVATE_KEY", "").strip()
             or env.get("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()
             or env.get("GITHUB_APP_PRIVATE_KEY_FILE", "").strip())
    )


def build_app_jwt(env: Mapping[str, str], *, now: float | None = None) -> str:
    app_id = env.get("GITHUB_APP_ID", "").strip()
    if not app_id.isdigit():
        raise GitHubAuthError("GITHUB_APP_ID must be numeric")
    key = _private_key(env)
    if not key.strip():
        raise GitHubAuthError("GitHub App private key is not configured")
    try:
        import jwt
    except ImportError as exc:
        raise GitHubAuthError("PyJWT is required for GitHub App authentication") from exc
    issued_at = int((time.time() if now is None else now) - 60)
    # PyJWT 2.10 validates iss as a string; GitHub accepts the numeric App ID
    # represented as its decimal string.
    payload = {"iat": issued_at, "exp": issued_at + 540, "iss": str(app_id)}
    encoded = jwt.encode(payload, key, algorithm="RS256")
    return encoded.decode("ascii") if isinstance(encoded, bytes) else str(encoded)


def gh_cli_token(
    api_base: str = API_BASE,
    *,
    config_dir: str = "",
    routed_profile: bool = False,
) -> str:
    """Read a token for the API host without crossing routed-profile boundaries."""
    if routed_profile and not config_dir.strip():
        return ""
    child_env = None
    if config_dir.strip():
        child_env = os.environ.copy()
        for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
            child_env.pop(key, None)
        child_env["GH_CONFIG_DIR"] = str(Path(config_dir).expanduser())
    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--hostname", _hostname_for_api(api_base)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
            env=child_env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
