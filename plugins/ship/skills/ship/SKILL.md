---
name: ship
description: Use when the user asks to ship a PR.
---

# ship

One call of this skill is a whole ship run: review the PR, fix every problem the review files,
review again, until the gate settles. It runs here, in your session, so the user sees every fix
and the agent that knows the code makes it. Each review round runs in its own fork
(`Skill(ship:review)`), where Sol finds blind and a fresh agent triages, so the review stays
independent of you, the fixer.

The run ends only at one of these **end states**, and the report names which:

- `OPEN`: every problem closed, and a review covers the code. Safe to merge.
- `NEEDS_DECISION`: the review budget is spent or the loop churns; every problem is closed and
  the recommendation escalated.
- `YOUR CALL`: a problem only the user can decide, escalated with the question; every other
  problem closed.
- `MERGED`: the PR is already in; closing it out is `Skill(ship:close)`.
- `STOPPED`: the round couldn't run (Sol unavailable or failing), or the user stopped the run;
  released on the record.

The plugin enforces it. Recording a review, or closing a problem, holds this session: its Stop
hook refuses to end your turn while the gate is shut, and says what's left. `gh pr merge` of a
tracked PR is refused while the gate is shut.

**Never ship debt.** Every problem, at every severity including `low`, is fixed or, if it isn't
relevant, closed as `wontfix` with the reason. A spent budget limits review rounds, never fixes.

`feature` picks the gh account by itself. Your own `gh` and `git push` calls follow the same rule:
the active account for a repo owned by it or one of its orgs, else prefix
`GH_TOKEN=$(gh auth token --user <personal login>)`.

## The loop

1. **Review.** `Skill(ship:review)`, with `args` naming the PR (if the user named one) and anything
   the user asked of the review (a deeper one, a fresh one past an OPEN gate). Read its report.
   - `Blocked: no PR`: open a draft against the feature's parent (`gh pr create --draft --base
     <parent>`), then review again.
   - `Blocked:` the PR's base isn't the tracked parent: retarget it (`gh pr edit <PR#> --base
     <parent>`), then review again.
   - `Blocked:` Sol unavailable or failing: end `STOPPED`, with Sol's error. If this session
     holds the branch, `feature release --reason "<Sol's error>"` first.
   - `Gate: MERGED`: end `MERGED`; call `Skill(ship:close)`.
   - `Gate: OPEN`: go to step 4.
   - Any other gate: go on.
2. **Fix.** `EnterWorktree` with `path: <worktree>` from the report (never `name:`), unless you're
   in it: editing from the main checkout edits the base branch. Then take every open problem
   (`feature problem list --open`), highest severity first, and close each:
   - Read it (`gh issue view <#>`): the repro, its evidence, the suggested fix.
   - Fix it at its root cause, not the symptom. Prefer deleting or narrowing over new code, and
     keep the diff to what the problem needs. Show the fix works: the repro or a test that fails
     before it and passes after.
   - Commit the fix apart from other work (a commit that fixes only `low` problems needs no
     review), push, then `feature problem resolve <#> --commit <sha>…` with every commit of the fix.
   - Not relevant (wrong, by design, out of this PR's scope): `feature problem wontfix <#> --reason
     "<evidence>"`, changing no code.
   - A choice only the user can make, which neither the code nor their request settles: leave it
     open and write down the question, the options and your recommendation.

   Done when each open problem is resolved, `wontfix`, or has its question written.
3. **Gate.** `feature gate`:
   - A problem left for the user: `feature escalate --problem <#>… --reason "<question, options,
     recommendation>"`, and end `YOUR CALL`. The CLI refuses while any other problem is open.
   - `REVIEW_AGAIN`: back to step 1, a fresh round over the whole PR.
   - `NEEDS_DECISION`: `feature escalate --reason "<recommendation>"`: review the fixes in one more
     round (`feature budget --set <n>`), merge them unreviewed, split the PR, or redesign it. When
     the gate names churn (fixes reopening old bugs, findings rising), say so: splitting or
     redesigning may beat another round. End `NEEDS_DECISION`.
   - `OPEN`: step 4.
4. **Finish at `OPEN`.** When the change can be run or seen and the description shows no proof it
   was, run it and add its before and after (`feature describe`); the gate doesn't move for a
   description. If the user asked to ship and the PR is a draft, `gh pr ready <PR#>`. The human
   merges; merge yourself only when the user asks.

If the user tells you to stop mid-run, `feature release --reason "<their words>"` and end
`STOPPED`.

## Rules

- Never lower a severity, or Sol's grade, to buy a cheaper review. Never review a round yourself:
  every round is `Skill(ship:review)`.
- `wontfix` is only for a problem that isn't relevant, never for a real one nobody has fixed yet.
- Close fixed problems with `feature problem resolve`, not a `Closes #a, #b` PR line (GitHub links
  only the first number).
- Merging past a shut gate is the user's decision, never yours. Told what's open, a user who still
  wants the merge gets it with `SHIP_PAST_GATE="<their reason>" gh pr merge …`, which records the
  reason on the tracking issue.
- Pre-existing bugs the review lists aren't this PR's and don't hold its gate. Ask the user, for
  each, whether to fix it in a separate PR (`Skill(ship:start)` off the base branch) or file an
  issue for it (`gh issue create`, with the failure scenario, `file:line` and the root-cause fix).
- The CLI fails fast. Report its errors, don't work around them.

## Report

End with only this, filled in:

```
Ship: <end state> — <one line: why it ended here>
Feature: <name>  Branch: <branch>  PR: <url>  Worktree: <abs path>
Rounds: <n> this run, Sol at <depth per round>; graded risk <…> (<door> door, blast radius <…>), complexity <…>
Fixed: #<n> [sev] <title> — <sha>   (one per line; or none)
Won't fix: #<n> [sev] <title> — <reason>   (one per line; or none)
Open: #<n> [sev] <title> — <the question for the user>   (only at YOUR CALL; else none)
Pre-existing: [sev] <title> — <file:line>, fix: <root-cause fix>   (or none)
Next: <what the user decides or does: merge, answer the question, pick the escalation's option>
```
