---
name: ship
description: Use when the user asks to ship a PR, or to babysit or watch one.
---

# ship

One call of this skill is a whole ship run: it takes the PR to ready to merge. It reviews the PR,
fixes every problem the review files and reviews again until the gate settles, then babysits the
PR through CI, its reviewers and its base. You drive the run here; the heavy work runs in forks, so
your thread keeps only their reports. Each review round is `Skill(ship:review)`, where Sol finds
blind, and each batch of fixes is `Skill(ship:fix)`.

The run ends at one of these **end states**, and your reply names which. Before you end at any of
them, `unwatch_pull_request` if you watched the PR.

- `READY`: the gate is `OPEN` and the PR is ready to merge (step 5).
- `NEEDS_DECISION`: the review budget is spent or the loop churns, escalated with your
  recommendation.
- `YOUR CALL`: something only the user can decide, asked with its options and your recommendation.
- `MERGED`: the PR is in; `Skill(ship:close)` closes it out.
- `STOPPED`: Sol can't run, the fix waits on something only the user has, or the user stopped
  the run; released on the record.

The plugin holds you to it. Once a round is recorded or a problem closed, the Stop hook refuses to
end your turn while the gate is shut, and `gh pr merge` of a tracked PR is refused until it opens.

**Never ship debt.** Every problem, at every severity including `low`, is fixed or, if it isn't
relevant, closed as `wontfix` with the reason. A spent budget limits review rounds, never fixes.

## The run

1. **Review.** `Skill(ship:review)`, with `args` naming the PR (if the user named one) and anything
   the user asked of the review (a deeper one, a fresh one past an `OPEN` gate). Then
   `EnterWorktree` with `path: <worktree>` from its report (never `name:`), unless you're in it:
   your own commands act on the branch they run in.
   - `Blocked: no PR`: open a draft against the feature's parent (`gh pr create --draft --base
     <parent>`), then review again.
   - `Blocked:` the PR's base isn't the tracked parent: `gh pr edit <PR#> --base <parent>`, then
     review again.
   - `Blocked:` Sol unavailable or failing: if this session holds the branch, `feature release
     --reason "<Sol's error>"`. End `STOPPED`, with Sol's error.
   - `Gate: MERGED`: end `MERGED`.
   - `Gate: NEEDS_DECISION` recommending a redesign: step 4, fixing nothing.
   - Any other gate: go on.
2. **Take in what the PR raised**, at its latest head:
   - A failed check. A flake (the infrastructure, not the repo's code): rerun it. A failure this PR
     causes: file it. One that fails on the base too is pre-existing (Rules); while it's a required
     check, the run ends `YOUR CALL`.
   - Review comments, from people and bots, that nobody has answered. Answer a question in its
     thread. File a requested change or a finding, unless a problem already covers it. A reply
     disputing a `wontfix` reopens it: `feature problem block <#> --reason "<their point>"`.
   - The base moved on: `mergeStateStatus` is `BEHIND` or `DIRTY`, or what landed on the base
     touches the PR's files. `feature sync`; a conflict it stops at goes to the fixer.

   File each with `feature problem add --title "…" --sev <sev> --body-file - <<'EOF' … EOF`: the
   source quoted with its URL, the failure, and `file:line`. `high`: data loss, a security hole, a
   crash or a silently wrong result on a realistic path; `med`: a real bug on a narrower path;
   `low`: anything else.
3. **Fix.** When a problem is open or a conflict waits, `Skill(ship:fix)`, with `args` naming the
   worktree, the PR, the conflict if any, and what you know that the code and the issues don't
   say: what the user asked of the change, a constraint they set, a fix they turned down. It fixes
   each open problem at its root cause or closes it as `wontfix`, and reports a line per problem.
   - `Question:` a choice only the user can make: step 4 escalates it.
   - `Not done:`, or a `Conflict:` it couldn't resolve: call it again, with what stopped it. If only
     the user can unblock it (access, a secret): `feature release --reason "<what stopped it>"`, and
     end `STOPPED`.
   - `Worktree:` changes left uncommitted that aren't the fixer's: tell the user in your reply.

   Then answer each comment step 2 filed or matched to a problem, in its thread, once that problem
   is closed: the fix commit, or why it won't be fixed. Resolve the thread. Done when each open
   problem is resolved, `wontfix`, or has its question, and each closed one's comment has its
   answer.
4. **Gate.** `feature gate`:
   - A problem left for the user: `feature escalate --problem <#>… --reason "<question, options,
     recommendation>"`, and end `YOUR CALL`.
   - `REVIEW_AGAIN`: step 1, a fresh round over the whole PR.
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
   - `OPEN`: step 5.
5. **Babysit.** When the change can be run or seen and the description shows no proof it was, run
   it and add its before and after (`feature describe`); the gate doesn't move for a description.
   If the user asked to ship it and it's a draft, `gh pr ready <PR#>`. The PR is **ready** when, at
   its head:
   - the required checks pass, and the review bots the repo uses have reviewed it;
   - every review thread is resolved, and no requested review is pending: once you've answered a
     person's comments, re-request their review;
   - nothing step 2 takes in is waiting: no comment unanswered, and the base hasn't moved on.

   Ready: end `READY`. The human merges; merge yourself only when the user asks. Not ready:
   `watch_pull_request` and end your turn. T3 Code wakes you when a check fails or the required
   ones pass, someone comments or reviews, or the branch conflicts. On each wake go to step 2, and
   end a turn that needed nothing without a message. A watch that stops with "could not read it"
   has usually spent the GraphQL rate limit: check `gh api rate_limit --jq .resources.graphql`, and
   watch again once it has reset.

If the user tells you to stop mid-run, `feature release --reason "<their words>"` and end
`STOPPED`.

## Rules

- Never lower a severity, or Sol's grade, to buy a cheaper review. Every round is
  `Skill(ship:review)` and every fix is `Skill(ship:fix)`: this thread reviews and fixes nothing
  itself.
- `wontfix` is only for a problem that isn't relevant, never for a real one nobody has fixed yet.
- Merging past a shut gate is the user's decision, never yours. Told what's open, a user who still
  wants the merge gets it with `SHIP_PAST_GATE="<their reason>" gh pr merge …`, which records the
  reason on the tracking issue.
- Pre-existing bugs (ones the review lists, a check that fails on the base too) aren't this PR's
  and don't hold its gate. Ask the user, for each, whether to fix it in a separate PR
  (`Skill(ship:start)` off the base branch) or file an issue for it (`gh issue create`, with the
  failure scenario, `file:line` and the root-cause fix).
- Post every comment and reply with `Skill(pr-comments:pr-comments)`.
- The CLI fails fast. Report its errors, don't work around them.

## Reply

Your last message is what the user reads, so end the run with a plain reply to them, not a
filled-in form. Open with the end state and, in a sentence, why the run ended there. Then, as
each applies:

- what the run fixed or closed as won't-fix: each problem's number and title, with its commit or
  the reason, from the fixer's reports;
- at `YOUR CALL`, each question with its options and your recommendation; at a redesign, which
  problems came back after their fix;
- the pre-existing bugs, each with the choice the Rules give the user;
- what the user does next: merge, answer a question, or pick the escalation's option.

Link the PR. Leave out what the PR body and the tracking issue already show, like the grade and
each round's depth.
