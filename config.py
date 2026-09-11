"""Configuration helpers for the standalone GitHub App plugin.

The plugin intentionally does not import Hermes' secret_scope or config internals.
Environment variables are read from the active process and, as a fallback for
Hermes gateway launches, the active Hermes home's .env file. Values are never
logged or returned from this module.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path
from typing import Mapping


_GITHUB_KEYS = frozenset(
    {
        "GITHUB_APP_ID",
        "GITHUB_APP_INSTALLATION_ID",
        "GITHUB_APP_PRIVATE_KEY_PATH",
        "GITHUB_APP_PRIVATE_KEY_FILE",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_SLUG",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    }
)


def hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".hermes"


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key not in _GITHUB_KEYS:
            continue
        try:
            parsed = shlex.split(value, comments=True, posix=True)
            values[key] = parsed[0] if parsed else ""
        except ValueError:
            # A malformed unrelated secret must not prevent the plugin from loading.
            values[key] = value.strip().strip("'\"")
    return values


def load_github_env() -> Mapping[str, str]:
    """Return GitHub settings with process environment taking precedence."""
    values = {key: os.environ[key] for key in _GITHUB_KEYS if os.environ.get(key)}
    active = hermes_home() / ".env"
    global_env = Path.home() / ".hermes" / ".env"
    # Load global first, then active profile; setdefault preserves process env and
    # lets a profile-specific file override the global fallback.
    for path in (global_env, active):
        for key, value in _parse_env_file(path).items():
            values.setdefault(key, value)
    # If both files were present, active profile should win only when the process
    # did not already provide a value.
    for key, value in _parse_env_file(active).items():
        if not os.environ.get(key):
            values[key] = value
    return values


def setting_bool(ctx: object | None, key: str, default: bool = False) -> bool:
    """Read a plugin-relative setting through the public context when available."""
    getter = getattr(ctx, "get_config", None)
    if not callable(getter):
        return default
    try:
        value = getter(key, default)
    except Exception:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)
