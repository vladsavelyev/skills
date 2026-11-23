---
name: ship
description: Use when the user asks to ship a PR.
---

# ship

One call of this skill is a whole ship run: review the PR, fix every problem the review files,
review again, until the gate settles. It runs here, in your session, and drives the loop; the
heavy work runs in forks, so your thread keeps only their reports. Each review round runs in its own
fork (`Skill(ship:review)`), where Sol finds blind and a fresh agent triages, so the review stays
independent of the fixes. Each round's fixes run in another (`Skill(ship:fix)`): the reading, edits
and test runs of fixing are most of what a run costs, and they would otherwise fill your thread.

The run ends only at one of these **end states**, and your reply names which:

- `OPEN`: every problem closed, and a review covers the code. Safe to merge.
- `NEEDS_DECISION`: the review budget is spent or the loop churns; every problem is closed and
  the recommendation escalated. When a kind of bug came back after its fix, the problems stay open
  instead, for the user's call on a redesign.
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
   Then `EnterWorktree` with `path: <worktree>` from it (never `name:`), unless you're in it: your
   own commands (`feature gate`, `escalate`, `release`, `describe`, `gh pr ready`) act on the
   branch they run on, and from another checkout they reach the wrong feature or none.
   - `Blocked: no PR`: open a draft against the feature's parent (`gh pr create --draft --base
     <parent>`), then review again.
   - `Blocked:` the PR's base isn't the tracked parent: retarget it (`gh pr edit <PR#> --base
     <parent>`), then review again.
   - `Blocked:` Sol unavailable or failing: end `STOPPED`, with Sol's error. If this session
     holds the branch, `feature release --reason "<Sol's error>"` first.
   - `Gate: MERGED`: end `MERGED`; call `Skill(ship:close)`.
   - `Gate: OPEN`: go to step 4.
   - `Gate: NEEDS_DECISION` recommending a redesign (a problem re-opened, or new ones like fixed
     ones): go to step 3 without fixing.
   - `Gate: NEEDS_DECISION` with no round run (the budget was already spent): go on; step 3
     escalates once the fixes are in.
   - Any other gate: go on.
2. **Fix.** `Skill(ship:fix)`, with `args` naming the worktree from the report and the PR, and
   what you know that the code and the issues don't say: what the user asked of the change, a
   constraint they set, a fix they turned down. It fixes each open problem at its root cause,
   commits, pushes and resolves it, or closes it as `wontfix`, and reports a line per problem.
   - `Question:` a choice only the user can make: step 3 escalates it.
   - `Not done:` call it again for those problems, with what stopped it. If only the user can
     unblock it (access, a secret), `feature release --reason "<what stopped it>"` and end
     `STOPPED`.
   - `Worktree:` changes left uncommitted that aren't the fixer's: tell the user in your reply.

   Done when each open problem is resolved, `wontfix`, or has its question.
3. **Gate.** `feature gate`:
   - A problem left for the user: `feature escalate --problem <#>… --reason "<question, options,
     recommendation>"`, and end `YOUR CALL`. The CLI refuses while any other problem is open.
   - `REVIEW_AGAIN`: back to step 1, a fresh round over the whole PR.
   - `NEEDS_DECISION` recommending a redesign (a kind of bug came back after its fix): the design
     keeps producing it, so the open problems wait. `feature escalate --reason "<what the recurring
     bugs have in common>"`, recommending a redesign over another round: a few competing designs,
     each critiqued blind by a separate agent, then one settled from the critiques. Name the shared
     cause, not a design of your own, and end `NEEDS_DECISION`. If the user picks the redesign, run
     that arena with agents, build the design it settles on, close each open problem with its
     commits, then `feature budget --set <n>` and ship again.
   - Any other `NEEDS_DECISION`: `feature escalate --reason "<recommendation>"`: review the fixes in
     one more round (`feature budget --set <n>`), merge them unreviewed, split the PR, or redesign
     it. When the gate names rising findings, say so: splitting may beat another round. End
     `NEEDS_DECISION`.
   - `OPEN`: step 4.
4. **Finish at `OPEN`.** When the change can be run or seen and the description shows no proof it
   was, run it and add its before and after (`feature describe`); the gate doesn't move for a
   description. If the user asked to ship and the PR is a draft, `gh pr ready <PR#>`. The human
   merges; merge yourself only when the user asks.

If the user tells you to stop mid-run, `feature release --reason "<their words>"` and end
`STOPPED`.

## Rules

- Never lower a severity, or Sol's grade, to buy a cheaper review. Never review a round yourself:
  every round is `Skill(ship:review)`. Never fix in this thread either: every fix is
  `Skill(ship:fix)`.
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

## Reply

Your last message is what the user reads, so end the run with a plain reply to them, not a
filled-in form. Open with the end state and, in a sentence, why the run ended there. Then, as
each applies:

- what the run fixed or closed as won't-fix: each problem's number and title, with its commit or
  the reason, from the fixer's reports;
- at `YOUR CALL`, each question with its options and your recommendation; at a redesign, which
  problems came back after their fix;
- the pre-existing bugs the review found, each with the choice the Rules give the user;
- what the user does next: merge, answer a question, or pick the escalation's option.

Link the PR. Leave out what the PR body and the tracking issue already show, like the grade and
each round's depth.
