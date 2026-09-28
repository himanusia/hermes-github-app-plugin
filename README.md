# Hermes GitHub App plugin

![Hermes GitHub App plugin social preview](assets/social-preview.jpg)

A standalone [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin
for GitHub issues and pull requests. It uses a GitHub App installation as the
preferred bot identity, shows the acting identity in every result, and requires
operator approval for each write.

The plugin lives outside the managed `hermes-agent` checkout, so Hermes updates
do not replace its source directory.

## Install

The drop-in directory is:

```text
$HERMES_HOME/plugins/github_app/
```

For the default profile this is `~/.hermes/plugins/github_app/`. Clone this
repository and copy its `github_app/` folder there:

```bash
git clone https://github.com/himanusia/hermes-github-app-plugin.git
mkdir -p ~/.hermes/plugins
cp -R hermes-github-app-plugin/github_app ~/.hermes/plugins/
hermes plugins enable github_app
```

The enable command installs the declared Python dependencies. Restart Hermes
after enabling the plugin. For a custom profile, replace `~/.hermes` with that
profile's `HERMES_HOME`.

Alternatively, install the distribution into Hermes' Python environment from
the repository root with `python -m pip install .`, enable it with
`hermes plugins enable github_app`, and restart Hermes. Hermes discovers the
package entry point at startup.

## Credentials

Recommended bot identity:

```text
GITHUB_APP_ID
GITHUB_APP_INSTALLATION_ID
GITHUB_APP_PRIVATE_KEY_PATH
```

Install the App on each repository Hermes should access. Give it **Metadata:
read**, **Issues: read and write** for issue creation/comments, and **Pull
requests: read and write** for reviews and merges. Grant only the permissions
needed for the tools you intend to use. Keep the private key outside the repo
and restrict its file permissions.

Put these credentials in the active Hermes profile's `.env` or secret scope.
`GITHUB_APP_PRIVATE_KEY` is also accepted for secret-manager injection, but a
file path is preferred. The plugin reads the active Hermes `.env` as a fallback
for gateway processes that do not inherit it. It never logs or returns token or
private-key values.

Fallbacks are `GITHUB_TOKEN`, `GH_TOKEN`, and an already-authenticated `gh`
CLI. App credentials always win over human credentials. A partial or invalid
App setup fails closed instead of silently switching to a human token. Run
`github_identity` after setup to confirm the actor and accessible repositories.

## GitHub Enterprise

`GITHUB_API_URL` sets the process-wide REST API base. For profiles that need a
different host, set the per-profile plugin setting
`plugins.entries.github_app.settings.api_url`, which takes precedence over the
process variable. Do not put this non-secret URL in `.env`.

```yaml
plugins:
  entries:
    github_app:
      settings:
        api_url: https://ghe.example.com/api/v3
        # Optional: required to use `gh` CLI auth from a routed profile.
        gh_config_dir: /path/to/that-profile/gh-config
```

GitHub.com defaults to `https://api.github.com`; GitHub Enterprise Server uses
`https://HOSTNAME/api/v3`. Only HTTPS URLs without userinfo, query strings, or
fragments are accepted. The `gh` CLI fallback is scoped to the matching
hostname. In multi-profile/routed sessions it is disabled unless that profile
has its own `gh_config_dir`; inherited process tokens are removed before the
CLI runs. Profile-scoped `GITHUB_TOKEN`/`GH_TOKEN` credentials remain supported.

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
`owner/name`; bare names are rejected rather than guessed. Every write also goes
through Hermes' request-bound operator approval gate, in addition to requiring
`allow_write_actions`.

For `github_review_pr`, supply the current full PR head SHA as `commit_id`.
For `github_merge_pr`, supply it as `expected_head_sha`. This guards against
acting on a head that changed after inspection.

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
python -m pip install -e . pytest pyyaml
python -m pytest tests/test_github_app.py -q
```

The tests use a fake transport and never contact GitHub.
