"""Standalone GitHub App JWT and credential helpers."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Mapping

from .config import hermes_home


class GitHubAuthError(RuntimeError):
    """A local credential/configuration error without secret-bearing details."""


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


def gh_cli_token() -> str:
    """Read a token from an already-authenticated gh CLI without printing it."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
