from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

import pytest

from github_app.auth import GitHubAuthError
from github_app.client import GitHubClient, GitHubError, parse_repo
from github_app.tools import build_handlers
import github_app
from github_app import config as config_module


class FakeContext:
    def __init__(
        self, *, allow_write: bool = False, api_url: str = "", gh_config_dir: str = ""
    ) -> None:
        self.allow_write = allow_write
        self.api_url = api_url
        self.gh_config_dir = gh_config_dir
        self.registrations: list[dict[str, Any]] = []

    def register_tool(self, **kwargs: Any) -> None:
        self.registrations.append(kwargs)

    def register_hook(self, name: str, callback: Any) -> None:
        self.hooks = getattr(self, "hooks", {})
        self.hooks[name] = callback

    def get_config(self, key: str, default: Any = None) -> Any:
        if key == "allow_write_actions":
            return self.allow_write
        if key == "api_url":
            return self.api_url or default
        if key == "gh_config_dir":
            return self.gh_config_dir or default
        return default


def test_parse_repo_requires_owner_and_name() -> None:
    assert parse_repo("himanusia/hermes-agent") == ("himanusia", "hermes-agent")
    for value in (
        "hermes-agent", "owner/", "owner/name/extra", "../user", "owner/..", "./user", "owner/.",
    ):
        with pytest.raises(GitHubError):
            parse_repo(value)


def test_load_github_env_isolated_by_active_profile_a_b_a(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from contextvars import ContextVar

    profile_a = tmp_path / "profiles" / "a"
    profile_b = tmp_path / "profiles" / "b"
    profile_a.mkdir(parents=True)
    profile_b.mkdir(parents=True)
    (profile_a / ".env").write_text("GITHUB_API_URL=https://a.example/api/v3" + chr(10), encoding="utf-8")
    (profile_b / ".env").write_text("GITHUB_API_URL=https://b.example/api/v3" + chr(10), encoding="utf-8")

    active_home = ContextVar("test_active_profile_home", default=profile_a)
    profile_secrets = {
        profile_a: {"GITHUB_TOKEN": "profile-a-token"},
        profile_b: {"GITHUB_TOKEN": "profile-b-token"},
    }
    monkeypatch.setattr(config_module, "_get_active_hermes_home", active_home.get)
    monkeypatch.setattr(
        config_module,
        "_profile_secret",
        lambda name: profile_secrets[active_home.get()].get(name),
    )
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "launch-profile"))
    monkeypatch.setenv("GITHUB_TOKEN", "launch-profile-token")
    monkeypatch.delenv("GITHUB_API_URL", raising=False)
    monkeypatch.setattr(config_module, "profile_scope_is_routed", lambda: True)

    result_a_first = config_module.load_github_env()
    active_home.set(profile_b)
    result_b = config_module.load_github_env()
    active_home.set(profile_a)
    result_a_again = config_module.load_github_env()

    assert result_a_first["GITHUB_TOKEN"] == "profile-a-token"
    assert result_a_first["GITHUB_API_URL"] == "https://a.example/api/v3"
    assert result_b["GITHUB_TOKEN"] == "profile-b-token"
    assert result_b["GITHUB_API_URL"] == "https://b.example/api/v3"
    assert result_a_again == result_a_first


def test_process_api_url_is_available_during_routed_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config_module, "_profile_secret", lambda name: {"GITHUB_TOKEN": "profile-token"}.get(name))
    monkeypatch.setattr(config_module, "_get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(config_module, "profile_scope_is_routed", lambda: True)
    monkeypatch.setenv("GITHUB_API_URL", "https://enterprise.example/api/v3")
    resolved = config_module.load_github_env()
    assert resolved["GITHUB_TOKEN"] == "profile-token"
    assert resolved["GITHUB_API_URL"] == "https://enterprise.example/api/v3"


def test_routed_profile_gh_cli_fallback_requires_a_profile_config_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    import github_app.client as client_module

    monkeypatch.setattr(
        client_module,
        "load_github_env",
        lambda _ctx=None: {"GITHUB_API_URL": "https://ghe.example/api/v3"},
    )
    monkeypatch.setattr(client_module, "profile_scope_is_routed", lambda: True)
    monkeypatch.setattr(
        client_module,
        "gh_cli_token",
        lambda *args, **kwargs: pytest.fail("must not query launch-profile gh credentials"),
    )
    assert client_module.github_credentials_available() is False


def test_routed_profile_gh_cli_uses_profile_config_and_scrubs_process_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    import github_app.auth as auth_module

    monkeypatch.setenv("GH_TOKEN", "launch-profile-gh-token")
    monkeypatch.setenv("GITHUB_TOKEN", "launch-profile-github-token")
    calls: list[dict[str, Any]] = []

    class Result:
        returncode = 0
        stdout = "profile-token" + chr(10)

    def run(command: list[str], **kwargs: Any) -> Result:
        calls.append(kwargs)
        return Result()

    monkeypatch.setattr(auth_module.subprocess, "run", run)
    assert auth_module.gh_cli_token(
        "https://ghe.example/api/v3",
        config_dir="/profiles/secondary/gh",
        routed_profile=True,
    ) == "profile-token"
    child_env = calls[0]["env"]
    assert child_env["GH_CONFIG_DIR"] == "/profiles/secondary/gh"
    assert "GH_TOKEN" not in child_env and "GITHUB_TOKEN" not in child_env


def test_github_enterprise_url_can_come_from_plugin_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config_module, "_profile_secret", lambda _name: None)
    monkeypatch.setattr(config_module, "_get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setenv("GITHUB_API_URL", "https://global.example/api/v3")
    ctx = FakeContext(api_url="https://ghe.example/api/v3")
    assert config_module.load_github_env(ctx)["GITHUB_API_URL"] == "https://ghe.example/api/v3"


def test_routed_profile_ignores_process_gh_config_dir_without_profile_setting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config_module, "_profile_secret", lambda _name: None)
    monkeypatch.setattr(config_module, "_get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(config_module, "profile_scope_is_routed", lambda: True)
    monkeypatch.setenv("GH_CONFIG_DIR", "/launch-profile/gh")
    assert "GH_CONFIG_DIR" not in config_module.load_github_env()


def test_profile_plugin_setting_supplies_gh_config_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config_module, "_profile_secret", lambda _name: None)
    monkeypatch.setattr(config_module, "_get_active_hermes_home", lambda: tmp_path)
    monkeypatch.setattr(config_module, "profile_scope_is_routed", lambda: True)
    ctx = FakeContext(gh_config_dir="/profiles/secondary/gh")
    assert config_module.load_github_env(ctx)["GH_CONFIG_DIR"] == "/profiles/secondary/gh"


def test_env_parser_preserves_escaped_newlines_and_hashes() -> None:
    escaped_newline = "first" + chr(92) + "nsecond"
    quoted_value = '"' + escaped_newline + '"'
    assert config_module._parse_env_value(quoted_value) == escaped_newline
    assert config_module._parse_env_value('"token#fragment"') == "token#fragment"
    assert config_module._parse_env_value("token # comment") == "token"


def test_github_app_identity_wins_over_pat_and_is_attributed() -> None:
    calls: list[tuple[str, str]] = []
    env = {
        "GITHUB_APP_ID": "123",
        "GITHUB_APP_INSTALLATION_ID": "42",
        "GITHUB_APP_PRIVATE_KEY": "fake-key-material",
        "GITHUB_APP_SLUG": "jarpis-bot",
        "GITHUB_TOKEN": "human-token",
    }

    def transport(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], Any]:
        calls.append((method, url))
        if url.endswith("/app/installations/42/access_tokens"):
            return 201, {}, {"token": "installation-token", "expires_at": "2099-01-01T00:00:00Z"}
        if url.endswith("/installation/repositories"):
            return 200, {}, {"repositories": [{"full_name": "himanusia/hermes-agent"}]}
        raise AssertionError(f"unexpected request: {method} {url}")

    client = GitHubClient(env=env, transport=transport)
    client._build_app_jwt = lambda: "app-jwt"  # type: ignore[method-assign]

    info = client.verify_identity()

    assert info["auth_method"] == "github-app"
    assert info["actor"] == "jarpis-bot[bot]"
    assert info["accessible_repos"] == ["himanusia/hermes-agent"]
    assert calls[0] == ("POST", "https://api.github.com/app/installations/42/access_tokens")
    assert all("/user" not in url for _, url in calls)


def test_pat_identity_falls_back_when_app_is_not_configured() -> None:
    calls: list[str] = []
    env = {"GITHUB_TOKEN": "human-token"}

    def transport(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], Any]:
        calls.append(url)
        if url.endswith("/user"):
            return 200, {}, {"login": "himanusia", "type": "User"}
        if url.endswith("/user/repos"):
            return 200, {}, [{"full_name": "himanusia/hermes-agent"}]
        raise AssertionError(f"unexpected request: {method} {url}")

    info = GitHubClient(env=env, transport=transport).verify_identity()

    assert info["auth_method"] == "pat"
    assert info["actor"] == "himanusia"
    assert info["accessible_repos"] == ["himanusia/hermes-agent"]
    assert calls == ["https://api.github.com/user", "https://api.github.com/user/repos"]


def test_enterprise_url_routes_app_and_client_requests_without_double_slashes() -> None:
    base = "https://ghe.example/api/v3/"
    calls: list[tuple[str, str]] = []
    env = {
        "GITHUB_API_URL": base,
        "GITHUB_APP_ID": "123",
        "GITHUB_APP_INSTALLATION_ID": "42",
        "GITHUB_APP_PRIVATE_KEY": "fake-key-material",
        "GITHUB_APP_SLUG": "jarpis-bot",
    }

    def transport(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], Any]:
        calls.append((method, url))
        if url.endswith("/app/installations/42/access_tokens"):
            return 201, {}, {"token": "installation-token"}
        if url.endswith("/installation/repositories"):
            return 200, {}, {"repositories": []}
        raise AssertionError(f"unexpected request: {method} {url}")

    client = GitHubClient(env=env, transport=transport)
    client._build_app_jwt = lambda: "app-jwt"  # type: ignore[method-assign]
    client.verify_identity()

    assert calls == [
        ("POST", "https://ghe.example/api/v3/app/installations/42/access_tokens"),
        ("GET", "https://ghe.example/api/v3/installation/repositories"),
    ]


def test_enterprise_pat_requests_use_configured_api_base() -> None:
    calls: list[str] = []

    def transport(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], Any]:
        calls.append(url)
        if url.endswith("/user"):
            return 200, {}, {"login": "operator", "type": "User"}
        if url.endswith("/user/repos"):
            return 200, {}, []
        raise AssertionError(f"unexpected URL: {url}")

    GitHubClient(
        env={"GITHUB_API_URL": "https://ghe.example/api/v3/", "GITHUB_TOKEN": "token"},
        transport=transport,
    ).verify_identity()

    assert calls == ["https://ghe.example/api/v3/user", "https://ghe.example/api/v3/user/repos"]


def test_enterprise_base_rejects_insecure_or_ambiguous_urls() -> None:
    for base in (
        "http://ghe.example/api/v3",
        "https:///api/v3",
        "https://user:password@ghe.example/api/v3",
        "https://ghe.example/api/v3?next=other-host",
        "https://ghe.example/api/v3#fragment",
    ):
        with pytest.raises(GitHubError, match="GITHUB_API_URL"):
            GitHubClient(env={"GITHUB_TOKEN": "token", "GITHUB_API_URL": base})


def test_enterprise_gh_cli_token_uses_configured_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    import github_app.auth as auth_module

    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "host-token\n"

    def run(command: list[str], **kwargs: Any) -> Result:
        calls.append(command)
        return Result()

    monkeypatch.setattr(auth_module.subprocess, "run", run)
    assert auth_module.gh_cli_token("https://ghe.example/api/v3") == "host-token"
    assert calls == [["gh", "auth", "token", "--hostname", "ghe.example"]]


def test_enterprise_gh_cli_token_preserves_ipv6_and_port(monkeypatch: pytest.MonkeyPatch) -> None:
    import github_app.auth as auth_module

    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "host-token\n"

    def run(command: list[str], **kwargs: Any) -> Result:
        calls.append(command)
        return Result()

    monkeypatch.setattr(auth_module.subprocess, "run", run)
    assert auth_module.gh_cli_token("https://[2001:db8::1]:8443/api/v3") == "host-token"
    assert calls == [["gh", "auth", "token", "--hostname", "[2001:db8::1]:8443"]]


def test_enterprise_gh_cli_token_preserves_non_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    import github_app.auth as auth_module

    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "host-token\n"

    def run(command: list[str], **kwargs: Any) -> Result:
        calls.append(command)
        return Result()

    monkeypatch.setattr(auth_module.subprocess, "run", run)
    assert auth_module.gh_cli_token("https://ghe.example:8443/api/v3") == "host-token"
    assert calls == [["gh", "auth", "token", "--hostname", "ghe.example:8443"]]


def test_credentials_check_treats_invalid_api_base_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import github_app.client as client_module

    monkeypatch.setattr(
        client_module,
        "load_github_env",
        lambda _ctx=None: {"GITHUB_API_URL": "http://ghe.example/api/v3"},
    )
    assert github_app.github_credentials_available() is False


def test_app_token_mint_failure_does_not_fall_back_to_human_pat() -> None:
    env = {
        "GITHUB_APP_ID": "123",
        "GITHUB_APP_INSTALLATION_ID": "42",
        "GITHUB_APP_PRIVATE_KEY": "fake-key-material",
        "GITHUB_TOKEN": "human-token",
    }

    def transport(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], Any]:
        if url.endswith("/app/installations/42/access_tokens"):
            return 401, {}, {"message": "App credentials invalid"}
        raise AssertionError(f"unexpected fallback request: {method} {url}")

    client = GitHubClient(env=env, transport=transport)
    client._build_app_jwt = lambda: "app-jwt"  # type: ignore[method-assign]
    with pytest.raises(GitHubAuthError, match="GitHub App"):
        client.verify_identity()


def test_partial_github_app_configuration_fails_closed_instead_of_using_pat() -> None:
    env = {
        "GITHUB_APP_ID": "123",
        "GITHUB_APP_INSTALLATION_ID": "42",
        "GITHUB_TOKEN": "human-token",
    }

    def transport(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], Any]:
        raise AssertionError(f"must not call API using fallback credentials: {method} {url}")

    client = GitHubClient(env=env, transport=transport)
    with pytest.raises(GitHubAuthError, match="GITHUB_APP"):
        client.verify_identity()


def test_401_refreshes_the_credential_once() -> None:
    attempts = 0
    env = {"GITHUB_TOKEN": "token"}

    def transport(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], Any]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return 401, {}, {"message": "Bad credentials"}
        return 200, {}, {"number": 7, "title": "works"}

    result = GitHubClient(env=env, transport=transport).get_issue("himanusia", "hermes-agent", 7)

    assert result["number"] == 7
    assert attempts == 2


def test_client_close_clears_app_jwt_and_auth_metadata() -> None:
    client = GitHubClient(
        env={
            "GITHUB_TOKEN": "test-token",
            "GH_TOKEN": "fallback-token",
            "GITHUB_APP_PRIVATE_KEY": "test-" + "private-key-material",
            "GITHUB_API_URL": "https://ghe.example/api/v3",
        }
    )
    client._app_jwt = "test-app-jwt"
    client._method = "github-app"
    client._token = "x" * 20
    client.close()
    assert client._app_jwt == ""
    assert client._method == ""
    assert client._token == ""
    assert client.env == {}


def test_handlers_close_each_created_client_after_success_or_error() -> None:
    closed = 0

    class CloseableClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def list_issues(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
            return []

        def close(self) -> None:
            nonlocal closed
            closed += 1

    handlers = build_handlers(FakeContext(), client_factory=CloseableClient)
    handlers["github_list_issues"]({"repo": "owner/repo"})
    assert closed == 1


def test_github_write_approval_is_not_remembered_across_payloads() -> None:
    """The approval key is payload-specific so a prior allow cannot approve changed content."""
    ctx = FakeContext()
    github_app.register(ctx)
    hook = ctx.hooks["pre_tool_call"]
    a = hook("github_create_issue", {"repo": "owner/repo", "title": "A"})
    b = hook("github_create_issue", {"repo": "owner/repo", "title": "B"})
    assert a["rule_key"] != b["rule_key"]


def test_review_commit_id_is_top_level_and_inline_comment_has_valid_location() -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []
    expected_head_sha = "abcdef12" * 5

    def transport(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], Any]:
        payload = kwargs.get("payload", {})
        calls.append((method, url, payload))
        if method == "GET":
            return 200, {}, {"head": {"sha": expected_head_sha}}
        return 200, {}, {"id": 7, "state": "APPROVED"}

    client = GitHubClient(env={"GITHUB_TOKEN": "token"}, transport=transport)
    client.review_pull_request(
        "owner", "repo", 1, "APPROVE", commit_id=expected_head_sha,
        comments=[{"path": "app.py", "body": "Looks good", "line": 3, "side": "RIGHT"}],
    )
    assert [call[0] for call in calls] == ["GET", "POST"]
    assert calls[1][2]["commit_id"] == expected_head_sha
    assert "commit_id" not in calls[1][2]["comments"][0]
    with pytest.raises(GitHubError, match="line comments require side"):
        client.review_pull_request(
            "owner", "repo", 1, "COMMENT", commit_id=expected_head_sha,
            comments=[{"path": "a.py", "body": "x", "line": 1}],
        )


def test_review_rejects_invalid_or_stale_commit_sha_before_submission() -> None:
    calls: list[str] = []

    def transport(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], Any]:
        calls.append(method)
        return 200, {}, {"head": {"sha": "f" * 40}} if method == "GET" else {"id": 7}

    client = GitHubClient(env={"GITHUB_TOKEN": "token"}, transport=transport)
    with pytest.raises(GitHubError, match="full 40-character"):
        client.review_pull_request("owner", "repo", 1, "APPROVE", commit_id="reviewed-head")
    assert calls == []
    with pytest.raises(GitHubError, match="head changed"):
        client.review_pull_request("owner", "repo", 1, "APPROVE", commit_id="a" * 40)
    assert calls == ["GET"]


def test_directory_install_folder_contains_importable_package_and_manifest() -> None:
    import yaml

    plugin_dir = Path(__file__).resolve().parents[1] / "github_app"
    assert (plugin_dir / "__init__.py").is_file()
    manifest = yaml.safe_load((plugin_dir / "plugin.yaml").read_text())
    assert manifest["name"] == "github_app"
    assert manifest["python_dependencies"] == ["PyJWT>=2.8,<3", "cryptography>=42,<45"]
    assert (plugin_dir.parent / "github_app").resolve() == plugin_dir.resolve()


def test_pyproject_entrypoint_uses_hermes_plugin_discovery_group() -> None:
    config = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    entry_points = config["project"]["entry-points"]
    assert entry_points["hermes_agent.plugins"]["github_app"] == "github_app:register"
    assert "hermes.plugin" not in entry_points
    manifest = (Path(__file__).resolve().parents[1] / "github_app" / "plugin.yaml").read_text()
    assert "python_dependencies:" in manifest
    assert "pip_dependencies:" not in manifest


def test_merge_client_sends_expected_head_sha_to_github() -> None:
    calls: list[dict[str, Any]] = []

    def transport(method: str, url: str, **kwargs: Any) -> tuple[int, dict[str, str], Any]:
        calls.append({"method": method, "url": url, "payload": kwargs.get("payload")})
        return 200, {}, {"merged": True, "sha": "merge-sha"}

    expected_head_sha = "abcdef12" * 5
    result = GitHubClient(env={"GITHUB_TOKEN": "token"}, transport=transport).merge_pull_request(
        "owner", "repo", 8, expected_head_sha=expected_head_sha
    )
    assert result["merged"] is True
    assert calls[0]["payload"]["sha"] == expected_head_sha


@pytest.mark.parametrize("sha", ("abcdef1", "a" * 39, "z" * 40))
def test_merge_rejects_non_full_or_non_hex_head_sha(sha: str) -> None:
    client = GitHubClient(env={"GITHUB_TOKEN": "token"}, transport=lambda *args, **kwargs: (200, {}, {}))
    with pytest.raises(GitHubError, match="full 40-character"):
        client.merge_pull_request("owner", "repo", 8, expected_head_sha=sha)


def test_pre_tool_hook_requires_fresh_operator_approval_for_each_payload() -> None:
    ctx = FakeContext()
    github_app.register(ctx)
    hook = ctx.hooks["pre_tool_call"]
    args = {"repo": "owner/repo", "number": 8, "body": "reviewed"}
    first = hook("github_comment_issue", args)
    assert first["action"] == "approve"
    assert first["message"].startswith("Approve this GitHub write (github_comment_issue)")
    assert hook("github_get_issue", args) is None
    assert hook("github_comment_issue", {**args, "body": "different"})["rule_key"] != first["rule_key"]
    identical_a = hook("github_comment_issue", args, tool_call_id="reused-id")
    identical_b = hook("github_comment_issue", args, tool_call_id="reused-id")
    assert identical_a["rule_key"] != identical_b["rule_key"]


def test_review_tool_requires_full_commit_id() -> None:
    schema = next(spec[1] for spec in github_app.TOOL_SPECS if spec[0] == "github_review_pr")
    assert "commit_id" in schema["parameters"]["required"]


def test_merge_tool_requires_expected_sha() -> None:
    schema = next(spec[1] for spec in github_app.TOOL_SPECS if spec[0] == "github_merge_pr")
    assert "expected_head_sha" in schema["parameters"]["required"]


def test_external_plugin_registers_namespaced_tools_without_core_wiring() -> None:
    ctx = FakeContext()

    github_app.register(ctx)

    assert len(ctx.registrations) == 7
    assert {item["toolset"] for item in ctx.registrations} == {"github_app"}
    assert {item["name"] for item in ctx.registrations} == {
        "github_identity",
        "github_create_issue",
        "github_comment_issue",
        "github_list_issues",
        "github_get_issue",
        "github_review_pr",
        "github_merge_pr",
    }


def test_write_actions_are_fail_closed_until_operator_enables_them() -> None:
    factory_called = False

    def client_factory() -> Any:
        nonlocal factory_called
        factory_called = True
        raise AssertionError("write guard must run before client creation")

    handlers = build_handlers(FakeContext(allow_write=False), client_factory=client_factory)
    result = json.loads(
        handlers["github_comment_issue"](
            {"repo": "himanusia/hermes-agent", "number": 1, "body": "not sent"}
        )
    )

    assert result["success"] is False
    assert result["error"]["code"] == "write_actions_disabled"
    assert factory_called is False


def test_successful_write_result_contains_attribution() -> None:
    @dataclass
    class FakeClient:
        def comment_issue(self, owner: str, repo: str, number: int, body: str) -> dict[str, Any]:
            return {"id": 99, "html_url": "https://github.com/example/repo/issues/1#issuecomment-99"}

        def attribution(self) -> dict[str, str]:
            return {"auth_method": "github-app", "actor": "jarpis-bot[bot]"}

    handlers = build_handlers(FakeContext(allow_write=True), client_factory=lambda **_kwargs: FakeClient())
    result = json.loads(
        handlers["github_comment_issue"](
            {"repo": "himanusia/hermes-agent", "number": 1, "body": "sent by bot"}
        )
    )

    assert result["success"] is True
    assert result["attribution"] == {
        "auth_method": "github-app",
        "actor": "jarpis-bot[bot]",
    }
