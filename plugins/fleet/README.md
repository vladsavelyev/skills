# fleet

A Claude Code session on a remote machine (a T3 Code VM, an EC2) reads only that machine's
`~/.claude`. It doesn't see the laptop's skills, plugins, settings or `CLAUDE.md`, so anything added
on the laptop has to be carried over, or remote sessions quietly work without it. A repo's own
`.claude/` arrives through git; `fleet` syncs the per-user parts.

```sh
fleet            # report drift, change nothing; exit 1 if anything is out of sync
fleet --apply    # fix it, then report what's left for a human
```

The machine it runs on is the source of truth. The hosts it syncs, and what stays per machine, come
from `~/.config/fleet/config.toml`:

```toml
hosts = ["ubuntu@my-vm.example.com"]
machine_settings = ["env.DISPLAY", "hooks.Stop", "statusLine"]  # settings keys each machine keeps
machine_skills = ["dev-browser"]                                # ~/.claude/skills entries that stay here
helpers = ["git-pr-context"]                                    # commands the hooks and statusline call
agents_md = ".claude/plugins/marketplaces/vlad-skills/agents/AGENTS.md"  # optional, see below
```

What it covers:

- **Claude Code**: every machine on the newest version any of them runs.
- **Skill repos**: `~/.claude/skills/` entries are symlinks into git clones under the home directory.
  Each clone is fast-forwarded to its default branch on every machine, and a host gets the clones and
  links it lacks. A clone on another branch, ahead or dirty is left alone and reported.
- **Plugins**: this machine's enabled user-scope plugins are installed, enabled and updated on the
  hosts. Disabled and project-scope ones are skipped.
- **`~/.claude/CLAUDE.md`**: shared down to the line
  `<!-- fleet: everything below is this machine's own and is not synced -->`. Right below it,
  `--apply` writes a generated `### This machine` section on every machine: its hostname and OS, the
  whole fleet and how each host is reached, and that its memory is its own (so an agent on a host
  knows it can't see what was remembered on the laptop, and makes shared changes there). Hand-written
  text after that section is left alone.
- **`agents_md`** (optional): agent instructions that live in a repo instead of in `CLAUDE.md`. The
  shared part of `~/.claude/CLAUDE.md` must import it (`@~/<agents_md>`), the file must exist on
  every machine (a missing `@` import loads nothing, without a word). Codex has no imports, so
  wherever it's installed `--apply` generates `~/.codex/AGENTS.md`: that machine's copy of the file
  followed by its own part of `~/.claude/CLAUDE.md` (below the marker). It's a copy, so after the
  rules change Codex has them from the next `--apply`, which reports the file out of date until then.
  Pointing `agents_md` into a plugin marketplace clone means it's always the default branch, and the
  marketplace update at session start keeps Claude Code's import current.
- **`~/.claude/settings*.json`**: every key is copied except `machine_settings`. Values go over stdin
  and only key names are printed, since `env` often carries secrets.
- **Reported only**: missing `helpers` (a missing one fails silently, e.g. a statusline segment just
  disappears), and the T3 Code server version on each host (the app pins it to its own on every
  connect, so a mismatch means reconnecting the environment).

Every overwritten file keeps a `.bak-<timestamp>` copy next to it. Plugin updates apply from the next
Claude Code session.

Not covered, by hand:

| What | How to carry it over |
|---|---|
| MCP sign-ins | per-machine token store, can't be copied: run `/mcp` once in an interactive Claude on the host |
| Project-scope plugins | `claude plugin install <name>@<marketplace> --scope project` in the checkout |

The plugin puts `fleet` on the agent's PATH. For your own terminal, a wrapper that runs the installed
plugin's CLI:

```sh
cat > ~/.local/bin/fleet <<'SH'
#!/bin/sh
set -eu
root=$(jq -er '.plugins["fleet@vlad-skills"][] | select(.scope == "user") | .installPath' \
  "$HOME/.claude/plugins/installed_plugins.json")
exec "$root/bin/fleet" "$@"
SH
chmod +x ~/.local/bin/fleet
```

Tests and lint, from this directory: `uv run pytest -q` and `uv run ruff check .`.
