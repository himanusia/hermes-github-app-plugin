"""Hermes configuration helpers with profile-scoped secret resolution."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

try:
    from hermes_constants import get_hermes_home as _get_active_hermes_home
except ModuleNotFoundError as exc:
    if exc.name != "hermes_constants":
        raise
    _get_active_hermes_home = None


_GITHUB_KEYS = frozenset(
    {
        "GITHUB_APP_ID",
        "GITHUB_APP_INSTALLATION_ID",
        "GITHUB_APP_PRIVATE_KEY_PATH",
        "GITHUB_APP_PRIVATE_KEY_FILE",
        "GITHUB_APP_PRIVATE_KEY",
        "GITHUB_APP_SLUG",
        "GITHUB_API_URL",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    }
)


def hermes_home() -> Path:
    """Resolve the active profile home, including Hermes' context-local override."""
    if _get_active_hermes_home is not None:
        return Path(_get_active_hermes_home())
    raw = os.environ.get("HERMES_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".hermes"


def _profile_secret(name: str) -> str | None:
    """Read through Hermes' profile secret scope when running inside the host."""
    try:
        from agent.secret_scope import get_secret
    except ModuleNotFoundError as exc:
        if exc.name not in {"agent", "agent.secret_scope"}:
            raise
        return os.environ.get(name)
    return get_secret(name)


def _parse_env_value(raw: str) -> str:
    """Parse Hermes-style dotenv values while preserving escaped PEM newlines."""
    value = raw.strip()
    if value.startswith(("'", '"')):
        quote = value[0]
        i = 1
        while i < len(value):
            if quote == '"' and value[i] == chr(92):
                i += 2
                continue
            if value[i] == quote:
                inner = value[1:i]
                if quote == "'":
                    return inner
                parsed: list[str] = []
                i = 0
                while i < len(inner):
                    if inner[i] == chr(92) and inner[i + 1:i + 2] in {'"', chr(92)}:
                        parsed.append(inner[i + 1])
                        i += 2
                    else:
                        parsed.append(inner[i])
                        i += 1
                return "".join(parsed)
            i += 1
        return value
    if value.startswith("#"):
        return ""
    for i, char in enumerate(value):
        if char == "#" and i > 0 and value[i - 1].isspace():
            value = value[:i].rstrip()
            break
    return value


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
        values[key] = _parse_env_value(value)
    return values


def profile_scope_is_routed() -> bool:
    """Whether this call is serving a secondary/multiplexed profile."""
    try:
        from agent.secret_scope import serves_routed_profile
    except ModuleNotFoundError as exc:
        if exc.name not in {"agent", "agent.secret_scope"}:
            raise
        return False
    return bool(serves_routed_profile())


def load_github_env(ctx: object | None = None) -> Mapping[str, str]:
    """Return credentials and API settings from the active Hermes profile."""
    values: dict[str, str] = {}
    for key in sorted(_GITHUB_KEYS):
        value = _profile_secret(key)
        if value:
            values[key] = value
    active = hermes_home() / ".env"
    for key, value in _parse_env_file(active).items():
        if key in _GITHUB_KEYS:
            values.setdefault(key, value)

    # The process API URL is a non-secret deployment-wide route and remains
    # available even when secret_scope correctly hides launch-profile tokens.
    process_api_url = os.environ.get("GITHUB_API_URL", "").strip()
    if process_api_url:
        values["GITHUB_API_URL"] = process_api_url
    # Plugin settings are profile-specific and therefore take precedence over
    # the process-wide route when profiles target different GitHub hosts.
    api_url = setting_text(ctx, "api_url")
    if api_url:
        values["GITHUB_API_URL"] = api_url

    gh_config_dir = setting_text(ctx, "gh_config_dir")
    if gh_config_dir:
        values["GH_CONFIG_DIR"] = gh_config_dir
    elif not profile_scope_is_routed():
        process_gh_config_dir = os.environ.get("GH_CONFIG_DIR", "").strip()
        if process_gh_config_dir:
            values["GH_CONFIG_DIR"] = process_gh_config_dir
    return values


def setting_text(ctx: object | None, key: str, default: str = "") -> str:
    """Read one plugin-relative string setting through the public plugin context."""
    getter = getattr(ctx, "get_config", None)
    if not callable(getter):
        return default
    try:
        value = getter(key, default)
    except Exception:
        return default
    return str(value).strip() if value is not None else default


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
