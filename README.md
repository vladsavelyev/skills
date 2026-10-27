# skills

My Claude Code plugins, as a marketplace (`vlad-skills`). Each plugin lives in its own repo; this
one only lists them in [.claude-plugin/marketplace.json](.claude-plugin/marketplace.json).

| Plugin | What it is |
| --- | --- |
| [ship](https://github.com/vladsavelyev/ship) | Feature branches with worktrees, PRs, issue-tracked review problems and a merge gate. |
| [sync](plugins/sync) | Keeps this marketplace and its installed plugins updated at every session start. |

## Setup

Once per environment (needs `claude`, `jq`, and `uv` for the plugins' CLIs):

```sh
curl -fsSL https://raw.githubusercontent.com/vladsavelyev/skills/main/bootstrap.sh | sh
```

It adds the marketplace and installs every plugin it lists. After that, the `sync` plugin's
SessionStart hook updates the marketplace and its installed plugins in the background at every
session start; updates apply from the next session, so a push reaches an environment within
two session starts. A failed sync is printed at the next session
start. (Claude Code's own marketplace `autoUpdate` never fired in testing, hence the hook.)

## Adding a plugin

Give the repo a `.claude-plugin/plugin.json`, add an entry to `marketplace.json`, and run
`claude plugin validate .`. List a repo by its HTTPS URL (`"source": "url"`), not the `github`
shorthand: plugin updates clone the shorthand over SSH, which fails where there's no GitHub key. A plugin without a `version` is versioned by commit, so every push
counts as an update. Small skills that don't deserve their own repo can go under `plugins/<name>/`
here, with `"source": "./plugins/<name>"`.
