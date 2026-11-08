# skills

My Claude Code plugins, as a marketplace (`vlad-skills`). Every plugin lives here under
`plugins/<name>/` and is listed in [.claude-plugin/marketplace.json](.claude-plugin/marketplace.json).

| Plugin | What it is |
| --- | --- |
| [ship](plugins/ship) | Feature branches with worktrees, PRs, issue-tracked review problems and a merge gate. |
| [sync](plugins/sync) | Keeps this marketplace and its installed plugins updated at every session start. |
| [pr-comments](plugins/pr-comments) | Attribution, tone and safety checks for comments an agent posts on GitHub for me. Adapted from [t3dotgg/tstack](https://github.com/t3dotgg/tstack)'s `leaving-pr-comments`. |
| [babysit-pr](plugins/babysit-pr) | Watch a PR through CI and bot reviews in T3 Code, fix or dismiss findings, and keep it current with its (possibly stacked) base branch. Adapted from tstack's `babysit-pr`. |

## Setup

Once per environment (needs `claude`, `jq`, and `uv` for the plugins' CLIs):

```sh
curl -fsSL https://raw.githubusercontent.com/vladsavelyev/skills/main/bootstrap.sh | sh
```

It adds the marketplace and installs every plugin it lists. After that, the `sync` plugin's
SessionStart hook updates every plugin the marketplace lists in the background at every session
start, installing any that are new. Updates apply from the next session, so a push reaches an
environment within two session starts. A failed sync is printed at the next session start.
(Claude Code's own marketplace `autoUpdate` never fired in testing, hence the hook.)

## Adding a plugin

Put it under `plugins/<name>/` with a `.claude-plugin/plugin.json`, add an entry to
`marketplace.json` with `"source": "./plugins/<name>"`, and run `claude plugin validate .`. A
plugin without a `version` is versioned by commit, so every push counts as an update.

To list a plugin that lives in another repo, use its HTTPS URL (`"source": "url"`), not the
`github` shorthand: plugin updates clone the shorthand over SSH, which fails where there's no
GitHub key.
