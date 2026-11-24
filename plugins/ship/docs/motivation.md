# Motivation & design history

This records *why* ship is shaped the way it is — the decisions, the dead ends, and
the course-corrections from the session that built it. `design.md` describes the system as it
stands; this file explains how it got there and what we deliberately rejected. Read it before
making a structural change, so you don't reintroduce something we already backed out of.

## The problem

Work on many features in parallel with an AI agent: each feature needs a branch, a matching PR,
a matching worktree, and some features stack on each other. We wanted an issue tracker where a
ticket matches a feature, and where a review populates found problems with statuses — so that
once no *new* problems are found and all are resolved, the feature can merge and the ticket
closes. And a new session on an existing branch should reconstruct its context: base branch,
last prompt, last review, open problems.

## Decisions that stuck

**GitHub Issues are the source of truth, not GitHub Projects.** Projects is a nice human
dashboard but a poor machine-readable store (verbose GraphQL, weak sub-item modeling, rate
limits). Issues give org-wide access, permissions, notifications, and search for free. The
compromise that made Issues work as a *machine* store: a fenced ```json block between
HTML-comment markers in the feature issue body — humans read a normal issue, the CLI reads one
deterministic block. Review problems are **sub-issues** so each gets its own state and timeline.

**State lives remotely (Issues) keyed by branch, never in a file on the feature branch.** A
state file committed to the feature branch would churn and conflict on every rebase/restack
across a stack. Per-branch wiring (base branch, issue number) lives in git config instead.

**Don't reinvent stacking — delegate to git-town.** git-town stores the parent pointer in
`git-town-branch.<name>.parent`, which is exactly the "base branch" a session bootstrap needs.
We verified the interop empirically: our `start`/`adopt` write that key, and `git town
config get-parent` / `git town branch` read it back and render the full stack. `feature sync`
is likewise a thin wrapper over `git town sync` (recursive ancestor sync); we do **not**
implement restacking ourselves.

**The merge gate is a pure function** of the blocking-problem count and the last review. The
"clean pass after the last fix" condition is the subtle one — it forces at least one clean review
*after* the final fix, so a fix that introduces a new bug can't slip through. This is also why
`feature sync` clears `last_review` when the base advances: the prior clean pass no longer covers
the newly merged code. (The condition started as "the last review found *nothing*", which never
terminated — see course-correction 5.)

**Abort-fast, never hide problems.** Every subprocess wrapper crashes loudly rather than
returning an empty/default result. This earned its keep repeatedly: when a review subprocess
hit a transient API error or a timeout, the wrapper *crashed instead of recording a false clean
review*, so the gate was never wrongly opened. A killed run left no partial state.

## Course-corrections (things we built then backed out of)

These are the expensive lessons. Re-litigate them only with new evidence.

**1. Fingerprint-based dedup → semantic dedup.** The first review loop stamped a
`sha1(file + normalized-title)` fingerprint in each problem's body to detect duplicates and
regressions deterministically across runs. It worked, but it was machinery in service of a
headless reviewer we later removed (see #3). With reviews now running in-session, the reviewing
agent dedups by *meaning* (the same bug is titled differently every review) against the tracked
open/closed problems it already sees. The fingerprint stamping was removed as dead weight.

**2. Hand-rolled review prompt → built-in `/code-review`.** The reviewer started as a
hand-written prompt ("you are a senior reviewer, use your read-only tools to inspect
surrounding code…"). That was wrong twice over: it reimplemented a maintained skill, and the
"inspect surrounding code" instruction actively *amplified* the reviewer's exploration time.
The correction: invoke the real built-in `/code-review`, which already knows how to review and
runs its own multi-angle finders. We verified headlessly that `/code-review` + `--json-schema`
lands validated findings in the result envelope's `structured_output` — so structured capture
*was* possible, contradicting an earlier wrong assumption that it only posts PR comments.
(Note: a project-local `code-review` skill was retired; the built-in is the one to use.)

**3. Headless `claude -p` reviewer → in-session review.** Even with `/code-review`, spawning a
separate `claude -p` subprocess to run it was the deepest dead end. It was slow (~20–30 min per
run, a single long serial chain), and — the killer — a second long-lived `claude` subprocess
got reaped by the session manager after ~10 min, so runs failed non-deterministically.
Mitigations we tried and then abandoned: a 600s timeout (too aggressive — killed a healthy
review pass), then 1200s + `--timeout`, then parallelizing the verification pass with a thread
pool. All of that complexity evaporated once we moved the review **in-session**: no second
`claude` process to reap, no cold start. `feature review run`, its adversarial-verification
pass, the timeout knobs, and the concurrency were all removed; `feature review record` remains
for the agent to report the result.

**4. In-context finder → fresh-subagent finder.** Moving in-session (see #3) first ran the
finder in the agent's *own* context. That surfaced a subtler bug: re-running a review in the
same session poisons it. On run 2+ the agent remembers run 1, so instead of rediscovering issues
blind it drifts into "confirm the tracked problems got fixed" — a verification mindset that
misses bugs the fixes themselves introduced. Since the gate's safety hinges on the *final*
review being an independent sweep, this quietly undermined the whole point. The correction: run
the finder in a fresh **subagent** (a sub-task of the live session process — still nothing for
the session manager to reap), passing only the diff scope and *not* the tracked-problem list, so every run finds
blind. The parent keeps dedup + recording, because that half genuinely needs the tracked state.

**5. "Zero findings" gate → severity-aware, budgeted, tri-state gate.** The gate originally
demanded a review run that found *nothing at all*. In practice that state never arrived: the
reviewer is built to report at every altitude, so every round returned nits, plausible-but-unproven
issues, and pre-existing problems in files the PR merely touched. Fixing those produced a new round
with new findings, forever — and each round is the most expensive operation in the workflow. The
only exit was `feature merge --force`, which recorded no rationale, so the real decision ("this is
good enough") happened off the record.

The correction has four parts, and the important thing is *which* part is the fix: **severity is**.
Only `sev:high`/`sev:med` hold the gate now, and the clean-pass condition became
`new_blocking == 0` rather than `findings == 0` — which is all the original safety property ("a fix
must not introduce a new defect") ever required. The rest supports it: **dispositions**
(`defer`/`reject`, each with a reason posted on the sub-issue) give "ship it anyway" and "that's a
false positive" first-class, auditable forms instead of `--force`; a **review budget** (2–4 rounds,
auto-sized from diff size and repo-declared sensitive paths) bounds the spend; and a **third
verdict**, `NEEDS_DECISION`, makes "stop and let a human choose — ship, split, or redesign" a
machine-visible outcome, reached either when the budget is spent or early when the loop is visibly
churning (findings not decreasing, or fixes re-opening closed problems).

Two things we considered and did *not* do. We didn't make deferral close the problem or spin out a
duplicate follow-up issue — deferred problems stay open, keep their real severity, and get named in
the merge comment, because debt you can't see isn't tracked and two issues for one bug drift.
And we didn't add per-round scope narrowing (review only the fix commits on rounds 2+) even though
it would roughly halve the tokens: it trades away the property that makes stacked reviews correct by
construction — the finder gets a PR *number*, and the PR's base defines the diff. That one is still
open, deliberately.

**6. One skill for everything → `start` and `ship`.** The original skill created the tracking
issue with the branch, so every feature carried review machinery from its first minute, long before
anyone thought about review. Starting a feature now needs only a branch, a worktree and a draft PR
(`feature start`, the `start` skill); the tracking issue appears when the user asks to ship, via
`feature adopt`. At the same time the finder moved from Claude's `/code-review` in the fork to
**Sol** (Codex, delegated through T3), at a reasoning effort sized per round. A reviewer from
another model family doesn't share the author's blind spots, and sizing the depth stops a docs fix
and a migration from costing the same review. The fork still triages; Sol finds.

The first sizing had the fork read the whole diff to judge risk and complexity, then pick the
effort. We dropped it within the same PR: it spent an Opus read of the full diff on choosing a
number, right before Sol read it again, and broke on PRs over 300 files. Round one is now sized from
diff stats and paths alone, and Sol grades risk and complexity as part of its review; that grade
goes into the PR body and sizes the rounds after it.

The through-line of all three subprocess/reviewer corrections: **don't reimplement or spawn what the
surrounding tools already provide.** Delegate stacking to git-town, reviewing to `/code-review`, and run the review in the
session that's already open.

## Process notes

- **The tool was dogfooded on itself.** Running `feature review run` against its own code (back
  when it existed) surfaced four real bugs in this codebase — a hardcoded pagination cap, a
  blanket `except` that violated abort-fast, a closed-fingerprint regression hole, and an
  `adopt --base` override that was silently dropped — all fixed. Later, it drove a real
  frontend change (responsive status bar) end to end, where the (then-present) verification pass
  correctly refuted a plausible-but-false CSS finding before it could close the gate.
- **The tool was extracted from a larger project to its own repo** once it stabilized, to be
  usable across other repos before/without merging back. The original branch/PR/issue were
  closed and deleted to avoid divergence; this repo is the sole source of truth.

**7. Deferred debt left open → a decision at close-out.** Deferred and low problems stayed open
"so the debt stays visible", but after the merge nothing ever looked at them: the first feature to
ship under the split skills left two open under a closed parent, with no owner and no plan. Close-out
now refuses until each open problem is a `follow-up` (backlog, on purpose) or `wontfix` (closed as
not planned), and the human makes that call.

**8. Bugs only → bugs, plus spec gaps and design smells as debt.** Sol was told to find defects and
skip style and taste, so a PR that left out part of what was asked for, or bolted a feature on
where it should have been built in, passed as long as nothing crashed. Sol now also reports, under
their own headings, what the intent asked for that the diff lacks or never asked for, and breaches
of the repo's documented rules plus a fixed list of Fowler's code smells. Both axes come from
Matt Pocock's [`code-review`](https://github.com/mattpocock/skills/tree/main/skills/engineering/code-review)
skill (MIT, © 2026 Matt Pocock), whose smell list the brief adapts. His runs them as two separate
reviewers and reports without triage; here they ride in the same Sol run, so a round costs no
extra review, and go through the same verify-and-dedup step. They're always filed `low`: a missing
nice-to-have or a "possible Feature Envy" is a judgement call, and letting judgement calls hold the
gate is the endless loop #5 got out of. Something asked for but built wrong stays a defect, at its
real severity.

**9. Close-out waits for the gate → close-out is its own skill and records the gate.** `feature
merge` refused until the gate opened, and the gate wants a review covering HEAD. A PR admin-merged
with two commits after its last review could then close out only through another review, so the
`ship` skill, asked to close it out, reviewed a merged PR, and T3 showed a "Review PR" run that
could block nothing. The gate now says `MERGED` once the PR is in, `feature merge` records what was
outstanding instead of refusing, and close-out moved to a `close` skill that runs inline and never
reviews.

**10. A spent budget read as "stop fixing" → it limits reviews only.** At `NEEDS_DECISION` the gate
offered three ways out: ship the rest as debt, buy a round, or split/redesign. Fixing wasn't one
of them. With a small, real blocker left after round 2, the skill recommended deferring it, and
the agent asked the user for permission to fix it. Now `NEEDS_DECISION` still has the blockers
fixed. The human only decides whether another review round goes over the fixes, and deferral is
for a blocker the PR can't fix.

**11. Low problems shipped as debt → every open problem blocks.** Only high/med problems held the
gate; `sev:low` ones, `deferred` ones and close-out `follow-up`s shipped open as "tracked debt". On
navari PR #4617 round 2 came back `OPEN` with four low problems still open, and the skill said
"Tell the user it's safe to merge. Four low problems ship as debt." Nothing came back to them. Now
the gate stays shut while any problem is open. A problem is either fixed or closed as `wontfix`
because it isn't relevant (`reject` folded into `wontfix`), and `defer` and `follow-up` are gone.
Severity still feeds the churn signal, which reads high/med counts only. A PR merged past the gate
anyway is the one exception: its fix needs a new PR regardless, so close-out lets the user hand each
open problem to a separate fix PR or keep it as an issue (`problem hand-off`) instead of waiting.

**12. Every commit re-reviewed → only code no review has seen.** Once lows blocked (#11), their
fixes moved the head off the reviewed SHA, so a round that found only lows still bought another full
round. navari #4890 ran three rounds at high effort over four low findings (two docs, a test name, a
docstring), after fixes and parent merges that resolved a conflict in a decision doc, and each
re-read of the whole PR found fresh nits. Now a round is due only for a commit that adds code a
review must see: not a recorded fix of low problems, not a merge of the base git made by itself (a
squash-merged parent's commits count as base), not a change to docs and tests alone. On #4890's
commits that is one round, not three.

**13. The smallest fix → the root-cause fix.** Asking the reviewer and the caller for "the smallest
fix" steered both toward band-aids, which the next agent then copies. Findings now carry the
root-cause fix.

**14. A ship run that ended at the review → one that fixes until the gate settles.** `ship` ran as
a forked subagent that reviewed one round and handed the fixes back as a `Next:` list. On navari PR
#5355 the round filed #5400 (med) and returned `REVIEW_AGAIN` with "close every open problem, then
call `Skill(ship:ship)` again". The calling session didn't fix it, and at the user's next message
("admin-merge the PR") it merged the PR with #5400 open. Advice in a report is only as good as the
caller's next turn. Now `ship` runs inline and drives the loop itself: each round is the forked
`review` skill (Sol still finds blind, and a fresh fork still triages), and the fixes happen in the
caller's session, where the user sees them and the agent knows the code. Fixing inside the fork was
the other option, but it would have hidden the fixes from the user, handed them to an agent with
none of the feature's context, and left triage of round 2 to the agent whose fixes it was judging.
Two hooks make it hold: the session that records a round or closes a problem can't end its turn
until the gate is `OPEN` (short of an escalation or a release, both on the record), and `gh pr
merge` of a tracked PR is refused while the gate is shut unless the user's reason goes on the
tracking issue. `feature escalate` refuses while a problem nobody asked the user about is open, so
escalating isn't a way around fixing. Running inline had one side effect we missed at first: ship kept the fill-in report block it had written for its fork's caller, and that block now landed in the user's chat as the run's last message. The run now ends with a plain reply to the user; only the forked `review` round keeps a template, since an agent reads it.

**15. A bug back after its fix → recommend a redesign, not another round.** On navari #5176 every
one of five rounds filed new high/med problems, mostly graded right, and nearly all of them were one
kind of bug in a new place: the page said "Checked by RIO, nothing found" about data the run hadn't
checked. The PR made a blanket "checked" claim over an open-ended set of data setups, so each round
looked at more setups and found the next gap. The gate escalated from round 2 on (findings rising,
then a regression, then a spent budget), but its next step was always to fix every problem first,
and its options put another round ahead of a redesign. So each escalation recommended fixing them
all and one more round, and each redesign one agent built in between turned up the next gap.
What settled it, by hand, was several competing designs, each critiqued blind by a separate agent,
and one approach settled from the critiques. Now the review files a problem with `--repeats` when
a root-cause fix of an already-fixed one would have prevented it, and a round with such a
high/med find, or a regression, gets a next step that says to stop patching and recommends that
arena over another round. `feature escalate` takes it with the problems still open, since fixing
them first is the work a redesign throws away. On #5176 that is round 2, not after round 5.

**16. Fixing in the caller's thread → in a fixer fork.** #14 kept the fixes inline, so the user saw
them and the agent that knew the code made them. Measured on navari's first two inline runs (#5172
and #5266, from the API's own token counts), a run grew the caller's context by 69k and 53k tokens,
and ship's own text (the skill, the round reports, the Stop hook's reasons, `feature`'s output) was
13–15% of it. The rest was fixing: reading code (a third), edits whose scripts stay in context,
test runs, and thinking. Both threads were already long (339k and 771k) before the run began, and
the fixes pushed them toward compaction. Now each round's fixes run in `Skill(ship:fix)`, a fork
like the review's, and the caller keeps its report: a line per problem. That gives up two things
#14 wanted. The user watches the reports, not the edits, and the fixer starts cold, so the caller
hands it what the code and the issues don't say, and the fixer spends more tokens re-reading than
the caller would have. #14's third reason doesn't apply: the fixer is a separate fork from the
triager, so round 2's triage is still not the fixer's. The Stop hook's reason shrank with it: it
restated the gate's next step and the skill's fixing steps on every block; now it gives the gate
with its reasons, the open problems and a pointer back to the run.

**17. Comments left to the author → a fourth check in the review.** Agents write most of the code
ship reviews, and they narrate it: comments that restate the line below, record what the code used
to do, or justify a workaround instead of fixing it. Lint can't tell those from the few a reader
needs. It catches commented-out code and bare suppressions, but not narration, and a blanket ban
also hits the comments that explain a vendor's quirk. pstack's `no-comments` (MIT, © 2026 Lauren
Tan) settles it with a reviewer that didn't write the comments, since an author defends its own.
ship's reviewer already reads the diff blind, so the check rides in the same run, with
`comment-sicko`'s keep list adapted: license headers, external constraints, public API contracts,
issue links, comments the repo's rules ask for, and text the code reads at runtime. ship doesn't
spawn `comment-sicko` itself: that would tie ship to pstack, and `comment-sicko` deletes comments,
which a review round must never do. Like spec and design findings, comment findings are filed
`low`. The deletions go in one problem rather than one per comment, so the fixer closes them in one
commit that needs no further review. A comment that hides a surprise in our own code, or claims a
constraint a check could enforce, gets its own problem, because its fix changes code.

## Deliberately deferred

- Mirroring problems to PR review threads (native resolved-thread UX).
- Versioning state in git (an orphan `feature-state` branch) as a diffable backup — Issues +
  their API history cover the current need.
- Scoping middle review rounds to the fix commits (`<last_review.sha>..HEAD`) instead of the whole
  PR, with a full-scope final round. Roughly halves the tokens of a 3-round feature, but it breaks
  the "hand the finder a PR number" invariant that makes stacked-branch scope correct by
  construction. Worth revisiting if review cost becomes the binding constraint.
