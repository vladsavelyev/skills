# ship

A CLI + SessionStart hook for running multiple Claude features in parallel. A feature starts as
a branch, a worktree and a draft PR (the `start` skill); when it's shipped (the `ship` skill) it
gets a GitHub tracking issue and a review by Sol (Codex, via T3) at a reasoning effort sized from
the PR: real defects, plus spec gaps and design smells, and Sol's grade of the PR's risk and complexity goes into the PR body. Features can be **stacked** on each
other (via git-town). Review-found problems are tracked as GitHub sub-issues, and a **merge
gate** blocks until every one is closed and a review covers the branch head. A new Claude
session on an existing branch reconstructs full context (base branch, last prompt, last review,
open problems) from GitHub. Once the PR merges, the `close` skill closes the feature out: it
records what shipped and closes the tracking issue, and never reviews.

Before the merge the gate has three verdicts, because "review until a run finds nothing" never
terminates — a real reviewer always finds *something*. After it, a fourth ends the loop:

| `feature gate` | Exit | Meaning |
| --- | --- | --- |
| `OPEN` | 0 | No open problems, and a review has covered the current branch head. Ship it. |
| `REVIEW_AGAIN` | 10 | Open problems or unreviewed code left, review budget remains. |
| `NEEDS_DECISION` | 20 | Budget spent, or the loop is churning. Open problems still get closed; a human decides whether to review the fixes in another round, merge, split, or redesign. |
| `MERGED` | 30 | The PR is in. Nothing left to review; close the feature out. |

**Nothing ships as debt.** Every open problem blocks the gate, at every severity including `low`.
It's either fixed (`feature problem resolve` with its commit) or, if it isn't relevant, closed with
the `wontfix` label and the reason (`feature problem wontfix`). Only after a PR has merged past the
gate can an open problem be handed off (`feature problem hand-off`), to a separate fix PR or kept as
an issue, so the close-out needn't wait for the fix. Severity orders the work and feeds
the convergence trend; it doesn't decide what blocks. Reviews are budgeted (2–4 rounds, sized from
the diff) so a non-converging loop ends in a recorded decision rather than unbounded spend.

See [docs/design.md](docs/design.md) for the full design, and
[docs/motivation.md](docs/motivation.md) for the design history — why it's shaped this way and
what we tried and backed out of (read before making structural changes).

## Install

This repo is a Claude Code plugin, listed in the
[vlad-skills](https://github.com/vladsavelyev/skills) marketplace:

```sh
claude plugin marketplace add vladsavelyev/skills
claude plugin install ship@vlad-skills
```

The plugin ships the `start` and `ship` skills, the SessionStart hook, and the `feature` CLI. Its `bin/` is on
the agent's PATH, and `bin/feature` runs the CLI from the plugin's own checkout via `uv run`, so
the CLI always matches the skill it was shipped with.

For `feature` in your own terminal, put a wrapper on your PATH that runs the installed plugin's
CLI, so the terminal and agent sessions always run the same version. Don't `uv tool install` a
checkout: Claude Code appends plugin `bin/` dirs to PATH, so that install would shadow the
plugin's CLI in agent sessions too.

```sh
cat > ~/.local/bin/feature <<'SH'
#!/bin/sh
set -eu
root=$(jq -er '.plugins["ship@vlad-skills"][] | select(.scope == "user") | .installPath' \
  "$HOME/.claude/plugins/installed_plugins.json")
exec "$root/bin/feature" "$@"
SH
chmod +x ~/.local/bin/feature
```

## Prerequisites

- [`gh`](https://cli.github.com/) authenticated (`gh auth status`). With two accounts logged in, a
  work one active and a personal one: a repo owned by the active account or one of its orgs uses
  it, and any other repo makes `feature` use the personal account's token for its `gh` and
  `git push` calls, without `gh auth switch`.
- [`git-town`](https://www.git-town.com/) installed (`brew install git-town`) — needed for
  restacking stacked branches. Base-branch tracking works without it.
- [`jq`](https://jqlang.org/), for the terminal `feature` wrapper above. It assumes one user-scope
  install of the plugin.
- Optional, per repo: declare the paths where review mistakes are expensive, so a PR touching
  them earns an extra review round:
  ```sh
  git config --add feature.sensitive-path 'src/auth/*'
  git config --add feature.sensitive-path 'migrations/*'
  ```

Reviews are run by **Sol**, delegated through T3 Code (`delegate_task` to the Codex provider) at
the reasoning effort `feature depth` picked. Sol gets the **PR by number** and nothing about
earlier rounds, so each re-run finds blind; the `ship` skill's forked subagent then dedups the findings against tracked problems, files the new ones, and records the run
via this CLI. A linked PR is required — the PR diff defines the review scope (correct even for
stacked branches), and `feature review record` refuses to run without one. There is no headless
review subcommand.

## Quick start

```sh
feature start add-oauth --body "Users want Google login."   # branch + worktree + draft PR
# ... work, commit, push; then, to ship:
feature adopt                                  # labels, tracking issue; links the open PR
feature depth                                  # effort for Sol: from stats + paths, later Sol's grade
# Sol reviews PR #123 at that depth, the skill dedups vs tracked problems, then:
feature problem add --title "…" --sev high    # file each genuinely-new problem
feature review record --sha $(git rev-parse HEAD) --found 46 47 48 49 50 \
    --summary "Token refresh race + unchecked PKCE verifier; 3 nits filed as low" \
    --depth xhigh --risk high --complexity med --why "Token handling; refresh has ordering"
feature problem list --open                    # every one holds the merge, low included
# ... fix each, or close the ones that aren't relevant:
feature problem resolve 46 --commit abc1234
feature problem wontfix 48 --reason "verifier is validated upstream in middleware.py:40"
# re-review, then record it; the gate opens once nothing is open and the review covers HEAD:
feature review record --sha $(git rev-parse HEAD) --found 51 \
    --summary "Re-reviewed oauth.py; #46 fixed; one naming nit filed" \
    --depth max --risk high --complexity med --why "Token handling; refresh has ordering"
feature gate                                   # exit 0 = safe to merge, 20 = ask a human
```

`feature adopt` works the same for a branch that was never started with `feature start`.

## Commands

| Command | Does |
| --- | --- |
| `feature start <name> --body <motivation> [--base <b>] [--title <t>]` | Branch (off the fetched remote base) + worktree + empty first commit, pushed, + draft PR. No tracking issue. |
| `feature adopt [--branch <b>] [--base <b>]` | Wire an existing branch into tracking: tracking issue, linked PR. |
| `feature describe --body-file <f\|-> [--title <t>]` | Replace the PR's description (and title). Pass it with or without ship's block (e.g. the fetched body, edited); the PR keeps exactly one. |
| `feature depth` | Reasoning effort for the next review: from diff stats and paths before any review, then from the last review's grade. |
| `feature prompt <text>` | Record the latest prompt. |
| `feature pr <number>` | Link a PR to the feature. |
| `feature review record --sha <s> --found [<problem#> ...] [--regressions <n>] --summary <t> --depth <d> --risk <r> --complexity <c> --why <t>` | Record a review run with Sol's grade (moves the gate, writes the grade and one line per round into the PR body) and print the resulting verdict. |
| `feature problem add --title <t> --sev <high\|med\|low>` | Add a problem sub-issue (it blocks the gate until closed, at any severity). |
| `feature problem resolve <number> [--commit <sha>]` | Close a problem sub-issue as fixed. |
| `feature problem wontfix <number> --reason <why>` | Not relevant (false positive, by design, out of scope): label it `wontfix` (creating the label if the repo lacks it) and close it as not planned, with the reason. |
| `feature problem hand-off <number> --reason <why>` | Only once the PR has merged: hand an open problem to a separate fix PR, or keep it as an issue. Labels it `handed-off`; it stays open, outside the feature, and stops holding the close-out. |
| `feature problem block <number> --reason <why>` | Reopen a closed problem (a regression, or a `wontfix` overturned) so it blocks again. |
| `feature problem list [--open]` | List problem sub-issues with severity and whether each is blocking, handed off, fixed or `wontfix`. |
| `feature budget [--set <n> \| --auto]` | Show or override the review-round budget. |
| `feature escalate --reason <t>` | Park at `needs-decision` with your recommendation for a human. |
| `feature status [--branch <b>] [--json]` | Reconstruct and print feature state. |
| `feature sync [--branch <b>] [--stack]` | Sync the branch with its base via git-town; if the base advanced, invalidates the last review so the gate reopens. |
| `feature gate [--branch <b>]` | Exit 0 = OPEN, 10 = REVIEW_AGAIN, 20 = NEEDS_DECISION, 30 = MERGED (1 and 2 stay "the command failed"). |
| `feature merge [--branch <b>] [--force]` | Mark merged and close the issue (checks that the PR really merged and records what the gate still had outstanding; refuses while an open problem hasn't been handed off). |
| `feature reconcile [--branch <b>] [--dry-run]` | Sweep the repo: close out every feature whose PR already merged, close as `abandoned` the ones whose PR was closed unmerged, and repair ship's block on still-open PRs (stale link, duplicate blocks). |
| `feature migrate [--branch <b>]` | Upgrade a repo for this CLI version: (re-)create the workflow labels and the feature issue's state block. Run it once per repo after upgrading. |

## PR ↔ issue linkage

`feature pr` writes a `closes #<issue>` reference into ship's block of the PR body, so **merging the PR closes the
tracking issue** with nobody having to remember a follow-up command. It used to write a
non-closing `Part of #<issue>` on the theory that the umbrella issue should outlive its PR and be
closed deliberately by `feature merge` — in practice that step happens after the merge button, when
the PR has left the screen and the branch is deleted, so it was skipped essentially every time and
tracking issues accumulated open for PRs merged weeks earlier.

The keyword covers the common case but not all of it: GitHub only auto-closes on a merge into the
**default branch** (never for a stacked feature merged into its parent), and it writes no record —
the state block would stay at `in-review` and nothing would record what shipped. So:

```sh
feature reconcile --dry-run   # what's orphaned: merged PRs, PRs closed unmerged, stale links
feature reconcile             # close them out, record the outcome, repair open PRs' links
```

A PR **closed without merging** leaves the same orphan seen from the other side — an open tracking
issue for work that will never land — so those close too, with status `abandoned` rather than
`merged`: the record must not claim a merge that never happened. The exception is a branch since
re-proposed under a new PR; that feature is alive, so the sweep hands it back with the number to
`feature pr` instead of burying it.

`reconcile` reads GitHub only — no git, no branch, no worktree — so it works on features whose
branch was deleted months ago. `feature status` also flags a merged PR whose feature never closed
out, which puts the reminder in front of the next session on that branch.

## SessionStart hook

The plugin's `hooks/hooks.json` runs `feature-session-context` at session start, so every Claude
session on a tracked branch gets its context injected.

## Claude skill

`skills/start/SKILL.md` starts a feature; `skills/ship/SKILL.md` ships it (Sol review at a sized depth,
triage, gate); `skills/close/SKILL.md` closes it out after the merge. Keep them in sync with the
CLI: when you add or change a command, update the SKILL.md files in the same commit.

## Development

```sh
uv sync
uv run pytest        # unit tests (state block, gate, review/verify schemas)
uv run ruff check .
claude --plugin-dir .   # a session running this checkout's plugin instead of the installed one
```
