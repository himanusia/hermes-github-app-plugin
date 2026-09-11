# Standalone GitHub App connector for Hermes

This plugin lives outside the managed `hermes-agent` checkout. Hermes updates do
not replace its source directory.

## Install

The drop-in directory is:

```text
$HERMES_HOME/plugins/github_app/
```

For the default profile this is `~/.hermes/plugins/github_app/`. It can also be
packaged and installed with the `hermes.plugin` entry point in `pyproject.toml`.
The plugin uses only the Python standard library plus `PyJWT` and
`cryptography` for GitHub App signing.

Enable it by adding `github_app` to the existing `plugins.enabled` list. Do not
overwrite the list because it may contain other user plugins.

## Credentials

Recommended bot identity:

```text
GITHUB_APP_ID
GITHUB_APP_INSTALLATION_ID
GITHUB_APP_PRIVATE_KEY_PATH
```

`GITHUB_APP_PRIVATE_KEY` is also accepted for secret-manager injection, but a
file path is preferred. The plugin reads the active Hermes `.env` as a fallback
for gateway processes that do not inherit it. It never logs or returns token or
private-key values.

Fallbacks are `GITHUB_TOKEN`, `GH_TOKEN`, and an already-authenticated `gh`
CLI. App credentials always win over human credentials.

## Tools

- `github_identity` (read-only)
- `github_list_issues` (read-only)
- `github_get_issue` (read-only)
- `github_create_issue`
- `github_comment_issue`
- `github_review_pr`
- `github_merge_pr`

All tools are namespaced in the `github_app` toolset. Every result includes
`attribution: {auth_method, actor}`. Repository references must be explicit
`owner/name`; bare names are rejected rather than guessed.

Write actions are fail-closed. Enable them only in the plugin's settings:

```yaml
plugins:
  entries:
    github_app:
      settings:
        allow_write_actions: true
```

This setting only makes the tools available to execute; it is not authorization
for a particular issue comment, review, or merge. The normal operator approval
and review policy still applies.

## Test

```bash
PYTHONPATH="$HERMES_HOME/plugins" \
  "$HERMES_HOME/hermes-agent/venv/bin/python" -m pytest -q \
  "$HERMES_HOME/plugins/github_app/tests"
```

The tests use a fake transport and never contact GitHub.
