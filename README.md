# skills

My Claude Code plugins, as a marketplace (`vlad-skills`). Every plugin lives here under
`plugins/<name>/` and is listed in [.claude-plugin/marketplace.json](.claude-plugin/marketplace.json).

| Plugin | What it is |
| --- | --- |
| [ship](plugins/ship) | `start`: a feature's branch, worktree and draft PR. `ship`: have Sol review the PR at a depth sized from it, track the problems, gate the merge. |
| [sync](plugins/sync) | Keeps this marketplace and its installed plugins updated at every session start, and refuses edits to their installed copies (and to `~/.codex/AGENTS.md`), saying where to make the change instead. |
| [pr-comments](plugins/pr-comments) | Attribution, tone and safety checks for comments an agent posts on GitHub for me. Adapted from [t3dotgg/tstack](https://github.com/t3dotgg/tstack)'s `leaving-pr-comments`. |
| [babysit-pr](plugins/babysit-pr) | Watch a PR through CI and bot reviews in T3 Code, fix or dismiss findings, and keep it current with its (possibly stacked) base branch. Adapted from tstack's `babysit-pr`. |
| [fleet](plugins/fleet) | Keep the per-user Claude Code setup (version, skill repos, plugins, `~/.claude/CLAUDE.md`, settings) the same on every machine I work from. Moved from navari-stack's `nstack fleet`. |
| [bro](plugins/bro) | `/bro`: restate the last message in plain human language, with no jargon. From [cursor/plugins](https://github.com/cursor/plugins)' pstack (MIT, © Lauren Tan). |
| [cdt](plugins/cdt) | The `cdt` MCP (chrome-devtools-mcp) in every project, on the shared profile `nstack login` signs in. |
| [unslop](plugins/unslop) | `unslop`: cut AI tells (filler, AI vocabulary, em dashes, jargon, passive voice) from any writing, typed as `/unslop` or picked up by the agent when it writes prose for people. Adapted from pstack (MIT, © Lauren Tan). |

## Agent rules

[agents/AGENTS.md](agents/AGENTS.md) holds my rules for coding agents, on every machine and in every
project. `~/.claude/CLAUDE.md` imports it from the marketplace clone
(`@~/.claude/plugins/marketplaces/vlad-skills/agents/AGENTS.md`), and `fleet` generates
`~/.codex/AGENTS.md` from the same file plus the machine's own part of `~/.claude/CLAUDE.md`. The repo's own instructions are the root
[AGENTS.md](AGENTS.md).

Change the rules here, with a PR. The marketplace clone and `~/.codex/AGENTS.md` are copies: the
`sync` plugin refuses an agent's Edit or Write to them and says where the change goes, stops updating
a clone that was changed anyway (by Bash, or in Codex) and prints the changes at the next session
start, and `fleet` reports such a clone as a problem.

## Setup

Once per environment (needs `claude`, `jq`, and `uv` for the plugins' CLIs):

```sh
curl -fsSL https://raw.githubusercontent.com/vladsavelyev/skills/main/bootstrap.sh | sh
```

It adds the marketplace and installs every plugin it lists. After that, the `sync` plugin's
SessionStart hook updates every plugin the marketplace lists in the background at every session
start, installing any that are new. Updates apply from the next session, so a push reaches an
environment within two session starts. A failed sync is printed at the next session start, and so
are local changes in the marketplace clone, which stop the updates until they're discarded.
(Claude Code's own marketplace `autoUpdate` never fired in testing, hence the hook.)

## Adding a plugin

Put it under `plugins/<name>/` with a `.claude-plugin/plugin.json`, add an entry to
`marketplace.json` with `"source": "./plugins/<name>"`, and run `claude plugin validate .`. A
plugin without a `version` is versioned by commit, so every push counts as an update.

To list a plugin that lives in another repo, use its HTTPS URL (`"source": "url"`), not the
`github` shorthand: plugin updates clone the shorthand over SSH, which fails where there's no
GitHub key.
