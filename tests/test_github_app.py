from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from github_app.client import GitHubClient, GitHubError, parse_repo
from github_app.tools import build_handlers
import github_app


class FakeContext:
    def __init__(self, *, allow_write: bool = False) -> None:
        self.allow_write = allow_write
        self.registrations: list[dict[str, Any]] = []

    def register_tool(self, **kwargs: Any) -> None:
        self.registrations.append(kwargs)

    def get_config(self, key: str, default: Any = None) -> Any:
        if key == "allow_write_actions":
            return self.allow_write
        return default


def test_parse_repo_requires_owner_and_name() -> None:
    assert parse_repo("himanusia/hermes-agent") == ("himanusia", "hermes-agent")
    with pytest.raises(GitHubError):
        parse_repo("hermes-agent")
    with pytest.raises(GitHubError):
        parse_repo("owner/")
    with pytest.raises(GitHubError):
        parse_repo("owner/name/extra")


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

    handlers = build_handlers(FakeContext(allow_write=True), client_factory=lambda: FakeClient())
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
