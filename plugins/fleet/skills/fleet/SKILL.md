---
name: fleet
description: Keep the per-user Claude Code setup the same on every machine the user works from (the laptop and the remote T3 EC2) with `fleet` — Claude Code version, skill repos and links, plugins, `~/.claude/CLAUDE.md` and `~/.claude/settings*.json` — respecting what is each machine's own. TRIGGER when asked to "sync the fleet", sync or compare Claude Code setups across machines, carry a CLAUDE.md/settings/skill/plugin change over to the EC2 (or back), or when a remote T3 thread behaves as if it lacks a skill, plugin or instruction the laptop has.
---

# Sync the fleet (`fleet`)

```sh
fleet            # report drift, change nothing; exit 1 if anything is out of sync
fleet --apply    # fix it, then report what's left for a human
```

Run it **on the laptop**: the machine it runs on is the source of truth, and the hosts in
`~/.config/fleet/config.toml` follow it. Run the check first and read the
diff it prints: `--apply` overwrites the hosts' shared parts, so an edit made only on the EC2 would
be lost. Carry that edit to the laptop first.

What `--apply` does:

| What | How |
|---|---|
| Claude Code | `claude install <newest version any machine runs>` on the ones behind |
| Skill repos | fast-forwards each clone `~/.claude/skills` links into to its default branch, on every machine; clones and links what a host lacks. Leaves a clone on another branch, ahead or dirty alone, and reports it |
| Plugins | installs, enables and `claude plugin update`s this machine's enabled user-scope plugins on the hosts. Disabled and project-scope ones are skipped |
| `~/.claude/CLAUDE.md` | copies everything above the line `<!-- fleet: everything below is this machine's own and is not synced -->`. Below it, on every machine including this one, it writes a generated `### This machine` section (hostname, OS, the fleet, how it's reached, that its memory is its own) between its own begin/end markers; the hand-written text after that is left alone |
| `~/.claude/settings*.json` | copies every key except `machine_settings` (e.g. `env.DISPLAY`, `hooks.Stop`). Prints key names only: values carry secrets |

Report only: missing `helpers` (commands the hooks and statusline call), and T3 Code. T3 Code
already keeps itself in line: on every connect the app pins the remote's server to the app's own
version. A mismatch means the environment needs reconnecting in T3 Code.

Each overwritten file keeps a `.bak-<timestamp>` copy next to it. Plugin updates take effect from
the next Claude Code session.

Something machine-specific showing up as drift is not a reason to sync it: add it to
`machine_settings` / `machine_skills` in the config, or move CLAUDE.md text below the marker.
