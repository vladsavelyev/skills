---
name: ship
description: Use when the user asks to ship, review a PR or branch, check whether it can merge, or close out a merged feature.
context: fork
background: false
---

# ship

You run in a forked subagent. Do the one job the caller asked for (a review round, a gate check,
a resume or a close-out), then end with the **report** below. The caller never sees this file,
so the report must tell it everything it needs.

The `feature` CLI (`plugins/ship` in the `vladsavelyev/skills` repo, design in its
`docs/design.md`) keeps all state in GitHub + git config. A tracked feature is a branch, its
worktree under the main checkout's `.claude/worktrees/<name>/`, its PR, and a tracking issue.
Review findings become sub-issues. The merge gate opens when nothing blocking is open and the last
review covers HEAD. Features begin untracked (`Skill(ship:start)`: branch, worktree, draft PR);
this skill adopts them on the first ship.

**Work in the feature worktree.** The main checkout has the same files on the base branch, so
reading or running there gives wrong answers. Prefix every command with `cd <worktree> &&`
(the shell's cwd resets between calls), and never read from the main checkout or another worktree.

`feature` picks the gh account by itself: the active one for a repo owned by it or one of its
orgs, the other logged-in account for any other repo. Your own `gh` and `git push` calls follow
the same rule, prefixing `GH_TOKEN=$(gh auth token --user <personal login>)` when it's personal.

## Commands

| Situation | Command |
| --- | --- |
| Track the branch | `feature adopt` (also creates the workflow labels if the repo lacks them) |
| Resume | `feature status`. If it warns merged-but-not-closed-out, run `feature merge` first. |
| Record the user's request | `feature prompt "<request>"` |
| Link the PR | `feature pr <number>` |
| Rewrite the PR description | `feature describe [--title "<t>"] --body-file - <<'EOF' … EOF` |
| Depth for the next round | `feature depth` |
| Record a review | `feature review record --sha <sha> --new-blocking <n> [--new-low <n>] [--regressions <n>] --summary "<sentence>" --depth <effort Sol ran at> --risk <r> --complexity <c> --why "<sentence>"` |
| Problems | `feature problem list --blocking\|--open`, `add`, `resolve <#> --commit <sha>`, `defer\|reject\|block <#> --reason "…"` |
| Review budget | `feature budget [--set <n> \| --auto]` |
| Stuck loop | `feature escalate --reason "<findings + recommendation>"` |
| Sync with base | `feature sync [--stack]` |
| Merge check | `feature gate`: exit 0 OPEN, 10 REVIEW_AGAIN, 20 NEEDS_DECISION; 1/2 mean the command failed |
| Close-out decision | `feature problem follow-up\|wontfix <#> --reason "…"` |
| Close out | `feature merge`, or `feature reconcile [--dry-run]` repo-wide |
| Old repo/issue after a CLI upgrade | `feature migrate` |

## Review round

1. **Track the PR.** Take the PR the caller named, else the current branch's open PR
   (`gh pr view --json number,headRefName,baseRefName,isDraft`). No PR → don't review; report
   that the caller must open one against the parent branch. The **worktree** is the checkout that
   has its branch (`git worktree list`). If none has it, run `gh pr checkout <PR#>` inside a new
   `git worktree add --detach .claude/worktrees/<branch>` from the main checkout.
   - Untracked branch (`feature status` says so): `feature adopt`. It links the open PR and takes the PR's base as the parent, or refuses if
     the branch's git-town parent differs.
   - Tracked branch with no PR linked: `feature pr <PR#>`.
   - The PR's base isn't the tracked parent: don't review. Report the mismatch.
   - Unpushed commits in the worktree: push them first. The review reads the PR, not the disk.
2. **Describe the PR.** A branch from `feature start` got its title and description before any
   code existed. When they no longer fit what the branch does (always on its first ship), rewrite
   them from the original motivation in the PR body and the commit messages
   (`git log --format='%s%n%b' origin/<base>..HEAD`); don't read the diff for this. The
   description is the motivation in one to three informal sentences plus, if it isn't obvious,
   what changed; no "Summary" header, no test plan. The title says what the PR does, in plain
   words. End it with your signature line if your harness asks for one. `feature describe` keeps
   the CLI's own meta below the prose and moves the signature to the very end. Sol and step 6 read this
   description to know the PR's intent, so it comes first.
3. **Pick the depth**: `feature depth` prints the reasoning effort for this round. Before any
   review it comes from the diff stats and the paths touched; after one, from the last review's
   grade. Don't read the diff to second-guess it. Go higher only when the user asked for a deeper
   review (`ultra` only on request), and say so in the report.
4. **Have Sol find, blind.** Sol is the Codex provider's Sol model in T3. Load the T3 tools with
   ToolSearch, call `orchestrator_capabilities` with `search: "sol"`, and take the newest Sol
   model the `codex` provider lists. Then `delegate_task` with `mode: "wait"`, `role: "review"`,
   `timeoutMs: 3600000`, and
   `target: {providerInstanceId: "codex", model: <sol model>, options: {reasoningEffort: <depth>}}`.
   The `task` is the brief below, and nothing about
   tracked problems or earlier rounds: Sol must find without them. If the wait times out, keep
   waiting (`t3_thread_wait` on the child thread) until it finishes; never cancel it or review
   yourself instead. If Sol isn't listed, or its task comes back failed (an auth or gateway
   error, a crash), stop: record no review, put Sol's error verbatim in the report, and don't
   substitute another reviewer.
   ```
   Review PR #<n> in <owner/repo>: `gh pr view <n>` and `gh pr diff <n>` (over 300 files that
   fails; then run `git fetch origin <base>` and use `git diff origin/<base>...HEAD` in the
   checkout instead). The PR's branch is
   checked out at <worktree abs path>; read code only there, never another checkout. Read-only:
   no edits, commits, pushes or GitHub comments.

   Find real defects this PR introduces: wrong results, crashes, data loss, security holes,
   broken edge cases on realistic paths, races, missing error handling, regressions in callers
   of changed code. Check each against the code before reporting it. Skip style and taste.

   For each finding give: a title; severity (high = data loss, security hole, crash or silently
   wrong result on a realistic path; med = a real bug on a narrower path; low = anything else);
   file:line; a concrete failure scenario (inputs or state → wrong outcome); the smallest fix.
   If you find nothing, say so plainly.

   Then grade the PR as a whole, each with low/med/high, plus one or two sentences on what
   drives both:
   - risk, what a missed bug would cost. high: data loss or corruption, security or auth, money,
     migrations or stored formats, concurrency, shared infra or deploys, anything hard to roll
     back. med: user-visible behaviour on a main path, public APIs, error handling. low: docs,
     tests, tooling, internal refactors with good coverage.
   - complexity, how hard the change is to get right. high: subtle state, ordering, caching, new
     invariants, many interacting parts. med: real logic in a few places. low: mechanical or local.
   ```
5. **Now load state**: `feature problem list --open`, plus closed problems for regression checks.
6. **Verify and dedup each finding** against the real code:
   - Can't reproduce it concretely → drop it.
   - Outside the PR's intent (other paths, rare cases, small perf) → `low`. So is code, a test or
     a guard the intent doesn't need.
   - Same meaning as an OPEN problem → skip.
   - Same as a CLOSED fixed problem → regression. `feature problem block <#> --reason
     "rediscovered: <repro>"`, and count it (if high/med) in `--new-blocking` and `--regressions`.
     Never `gh issue reopen`.
   - Same as a REJECTED problem → skip, unless the new repro answers the rejection reason. Then
     `block` it. A problem disputed twice goes to the human: escalate with both sides.
   - Same as a WONTFIX problem (closed as not planned) → skip, unless the new repro makes it worse
     than the reason allowed for. Then file it fresh at its real severity.
   - Not caused by this PR → never blocking. File it `--sev low`, or defer it.
   - Re-judge severity yourself, whatever Sol said: `high` = data loss, security hole, crash, or
     silently wrong result on a realistic path; `med` = real bug on a narrower path; `low` =
     everything else. Only `low` is non-blocking, and an unlabelled problem blocks, so always
     pass `--sev`.
7. **File new ones**: `feature problem add --title "…" --sev … --body-file - <<'EOF' … EOF`, with
   the repro, `file:line` and the suggested smallest fix.
8. **Record**: `feature review record --sha $(git rev-parse HEAD) …`. Write `--summary` as a real
   sentence about what was reviewed and found, never a bare "clean pass". Pass the effort Sol ran
   at as `--depth`, and Sol's grade as `--risk`, `--complexity` and `--why`; raise a grade if the
   findings show Sol underrated it, never lower one. The CLI writes the grade into the PR body and
   sizes the next round's depth from it.

## Gate verdict

- `OPEN` (0): safe to merge. Name the debt that ships. If the PR is still a draft, mark it ready
  (`gh pr ready <PR#>`) only when the caller asked to ship; for a plain review, say in `Next`
  that it's still a draft. The human merges.
- `REVIEW_AGAIN` (10): the caller fixes the blocking problems, then calls this skill again.
- `NEEDS_DECISION` (20): no more rounds. `feature escalate` with exactly one recommendation: ship
  it (defer the rest with reasons), buy one round (`feature budget --set`, only with a concrete
  reason), split the PR, or redesign.

## Close out

After the human merges, every problem still open needs a decision before the feature closes:
`feature merge` and `feature reconcile` refuse until there is one, because once the feature is
closed nothing comes back to an undecided problem. The two decisions:

- `feature problem follow-up <#> --reason "…"`: worth fixing later; stays open as a backlog issue.
- `feature problem wontfix <#> --reason "…"`: not worth fixing; closed as not planned.

Don't decide them yourself. List them in the report with one recommendation each and the reason,
and let the caller ask the user. Then run `feature merge`.

## Rules

- Never lower a severity, or Sol's risk or complexity grade, to open the gate or buy a cheaper
  review.
  Defer with a reason instead.
- Never `feature merge --force`. Never merge for the human.
- Close fixed problems with `problem resolve`, not a `Closes #a, #b` PR line (GitHub links only the
  first number).
- Don't hand-edit the JSON block in a feature issue, or the assessment section in a PR body.
- The CLI fails fast. Report its errors, don't work around them.

## Report

End with only this, filled in:

```
Feature: <name>  Branch: <branch>  PR: <url or none>  Worktree: <abs path>
Did: <one line>
Review: Sol <model> at <depth>; graded risk <…>, complexity <…>; next round at <depth>   (or not run)
Gate: <verdict, or not run>
Blocking: #<n> [sev] <title> — <file:line>, fix: <smallest fix>   (one per line, or none)
Next:
- <exact steps for the caller>
```

Always include these caller rules in `Next` when they apply:
- If the caller may not be in the worktree: "Call `EnterWorktree` with `path: <worktree>` (never
  `name:`) before editing. Editing from the main checkout edits the base branch."
- With blocking problems: "Fix each with the smallest change; prefer deleting or narrowing over new
  code, and don't grow the diff to appease the reviewer. Commit and push, then `feature problem
  resolve <#> --commit <sha>`. If a finding is wrong, `feature problem reject <#> --reason
  "<evidence>"` instead of changing code. Then call `Skill(ship:ship)` for the next round, without
  asking the user."
- With `OPEN`: "Tell the user it's safe to merge and what debt ships. After they merge, call
  `Skill(ship:ship)` to close out."
- With open problems at close-out: "Ask the user to decide each (follow-up or wontfix, with your
  recommendation), then call `Skill(ship:ship)` with their decisions to close out."
- With `NEEDS_DECISION`: "Stop. Present the recommendation to the user."
