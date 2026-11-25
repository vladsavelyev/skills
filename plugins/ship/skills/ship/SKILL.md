---
name: ship
description: Use when the user asks to ship a PR, or to babysit or watch one.
---

# ship

One call of this skill is a whole ship run: it takes the PR to ready to merge. It reviews the PR,
fixes every problem the review files and reviews again until the gate settles, then babysits the
PR through CI, its reviewers and its base. You drive the run; every review round is
`Skill(ship:review)` and every batch of fixes `Skill(ship:fix)`, each in a T3 child thread
(**Rounds** below), so this thread reviews and fixes nothing itself and the user can talk to you
while a round runs.

The run ends at one of these **end states**, and your reply names which. Before you end at any of
them, `unwatch_pull_request` if you watched the PR.

- `READY`: the gate is `OPEN` and the PR is ready to merge (step 5).
- `NEEDS_DECISION`: the review budget is spent or the loop churns, escalated with your
  recommendation.
- `YOUR CALL`: something only the user can decide, asked with its options and your recommendation.
- `MERGED`: the PR is in; `Skill(ship:close)` closes it out.
- `STOPPED`: Sol can't run, the fix waits on something only the user has, or the user stopped
  the run; released on the record (`feature release --reason "…"`).

**Never ship debt.** Every problem, at every severity including `low`, is fixed or, if it isn't
relevant, closed as `wontfix` with the reason. A spent budget limits review rounds, never fixes.

## Rounds

A round runs in a T3 child thread, never in this one: a skill called here holds this thread until
it returns, and every message the user sends waits with it.

1. **Start it.** `delegate_task` with `mode: "async"`, no `target` (the child runs as you do),
   `role: "review"` or `"implementation"`, `title: "Review PR #<n>"` or `"Fix PR #<n>"`, and `task:
   "Run Skill(ship:review) with args: <args>. Reply with only its report, verbatim."` (`ship:fix`
   for a fix round).
2. **Hold it.** `feature round --branch <the PR's head branch> --thread <childThreadId>`. While the
   thread runs you may end your turn; once it's done, the Stop hook keeps you going until the gate
   settles.
3. **End your turn.** The thread's completion wakes you with its report: go on from the step that
   started the round. Until then, answer whatever the user sends and do what they ask.

A round thread that fails or is cancelled wakes you too: start it again, or end `STOPPED` if it
fails the same way twice.

## The run

1. **Review.** A review round, with `args` naming the PR and anything the user asked of the
   review (a deeper one, a fresh one past an `OPEN` gate). Then `EnterWorktree` with `path:
   <worktree>` from its report (never `name:`), unless you're in it.
   - `Blocked:` no PR, or its base isn't the tracked parent: open a draft against the parent (`gh
     pr create --draft --base <parent>`) or retarget it (`gh pr edit <PR#> --base <parent>`), and
     review again.
   - `Blocked:` Sol unavailable or failing: end `STOPPED` with Sol's error, releasing the branch
     if this session holds it.
   - `Gate: MERGED`: end `MERGED`.
   - `Gate: NEEDS_DECISION` recommending a redesign: step 4, fixing nothing.
2. **Take in what the PR raised.**
   - A check this PR fails: file it.
   - A requested change or a finding in a review comment, from a person or a bot: file it, unless
     a problem already covers it. A reply disputing a `wontfix` reopens it: `feature problem block
     <#> --reason "<their point>"`.
   - A base that moved on (`BEHIND`, `DIRTY`, or changes to the PR's files): `feature sync`. A
     conflict it stops at goes to the fixer.

   File with `feature problem add --title "…" --sev <sev> --body-file -`, quoting the source with
   its URL. `high`: data loss, a security hole, a crash or a silently wrong result on a realistic
   path; `med`: a real bug on a narrower path; `low`: anything else.
3. **Fix.** When a problem is open or a conflict waits, a fix round, with `args` naming the
   worktree, the PR, the conflict if any, and what you know that the code and the issues don't
   say: what the user asked of the change, a constraint they set, a fix they turned down.
   - `Question:` a choice only the user can make: step 4 escalates it.
   - `Not done:`, or a `Conflict:` it couldn't resolve: start another with what stopped it, or end
     `STOPPED` if only the user can unblock it.

   Then answer each comment you filed or matched, once its problem is closed, and resolve the
   thread. Done when every open problem is resolved, `wontfix`, or has its question.
4. **Gate.** `feature gate`:
   - `MERGED`: end `MERGED`.
   - A problem left for the user: `feature escalate --problem <#>… --reason "<question, options,
     recommendation>"`, and end `YOUR CALL`.
   - `REVIEW_AGAIN`: step 1.
   - `NEEDS_DECISION`: follow its next step and end `NEEDS_DECISION`. Recommending a redesign, name
     the cause the recurring bugs share, not a design of your own. If the user picks the redesign,
     run its arena with agents, build what it settles on, close each open problem with its
     commits, then `feature budget --set <n>` and ship again.
   - `OPEN`: step 5.
5. **Babysit.** If the description shows no proof the change works and it can be run or seen, run
   it and add the before and after with `feature describe`. If the user asked to ship it and it's a
   draft, `gh pr ready <PR#>`. The PR is **ready** when, at its head:
   - the required checks pass, and the review bots the repo uses have reviewed it;
   - every review thread is resolved, and no requested review is pending: once you've answered a
     person's comments, re-request their review;
   - step 2 has nothing to take in.

   Ready: end `READY`, or, if the user asked you to merge it, merge it and end `MERGED`. Not ready:
   step 2 if it has something to take in, else `watch_pull_request` and end your turn. On each wake
   go to step 4, and end a turn that needed nothing without a message. A watch that stops with
   "could not read it" has usually spent the GraphQL rate limit (`gh api rate_limit --jq
   .resources.graphql`): watch again once it resets.

## Rules

- Never lower a severity, or Sol's grade, to buy a cheaper review.
- Pre-existing bugs (ones the review lists, a check that fails on the base too) aren't this PR's
  and don't hold its gate. Ask the user, for each, whether to fix it in a separate PR
  (`Skill(ship:start)` off the base branch) or file an issue for it (`gh issue create`, with the
  failure scenario, `file:line` and the root-cause fix).
- The CLI fails fast. Report its errors, don't work around them.

## Reply

Your last message is what the user reads, so end the run with a plain reply to them, not a
filled-in form. Open with the end state and, in a sentence, why the run ended there. Then, as
each applies:

- what the run fixed or closed as won't-fix: each problem's number and title, with its commit or
  the reason;
- at `YOUR CALL`, each question with its options and your recommendation; at a redesign, which
  problems came back after their fix;
- the pre-existing bugs, each with the choice the Rules give the user;
- what the user does next.

Link the PR. Leave out what the PR body and the tracking issue already show, like the grade and
each round's depth.
