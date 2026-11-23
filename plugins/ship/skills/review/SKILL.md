---
name: review
description: Use when `Skill(ship:ship)` runs a review round of a PR, or the user asks for a single review round without fixing.
context: fork
background: false
---

# review

You run in a forked subagent and do one review round of a PR: Sol finds, blind; you verify, dedup
and file what it found, and record the round. Then end with the **report** below. The caller is
usually `Skill(ship:ship)`, which fixes what you file and calls you again for the next round; it
never sees this file, so the report must carry everything it needs. Your arguments name the PR
(else take the current branch's) and anything the user asked of this round (a deeper review, a
fresh one past an OPEN gate).

You never fix. Keeping the fixer out of the round is what keeps it independent: you triage Sol's
findings with no memory of how the code got this way.

**Never ship debt.** Every problem a review files, at every severity including `low`, is closed
before the PR ships: fixed, or closed as `wontfix` because it isn't relevant. The gate stays shut
while any problem is open, so file every real finding: the caller fixes them all.

A merged PR is not yours: reviewing it can block nothing, and closing it out is
`Skill(ship:close)`. If the PR has merged, or the gate says `MERGED`, do nothing else and report
`MERGED`.

The `feature` CLI (`plugins/ship` in the `vladsavelyev/skills` repo, design in its
`docs/design.md`) keeps all state in GitHub + git config. A tracked feature is a branch, its
worktree under the main checkout's `.claude/worktrees/<name>/`, its PR, and a tracking issue.
Review findings become sub-issues. The merge gate opens when no problem is open and nothing since
the last review needs one (fixes of low problems, base merges, docs and tests don't). Features
begin untracked (`Skill(ship:start)`: branch, worktree, draft PR);
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
| Resume | `feature status`. If the gate says `MERGED`, stop and report it. |
| Record the user's request | `feature prompt "<request>"` |
| Link the PR | `feature pr <number>` |
| Rewrite the PR description | `feature describe [--title "<t>"] --body-file - <<'EOF' … EOF` |
| Depth for the next round | `feature depth` |
| Record a review | `feature review record --sha <sha> --found [<problem#> ...] [--regressions <n>] --summary "<sentence>" --depth <effort Sol ran at> --risk <r> --door <one-way\|two-way> --blast-radius "<words>" --complexity <c> --why "<sentence>"` |
| Problems | `feature problem list [--open]`, `add`, `block <#> --reason "…"` |
| Merge check | `feature gate`: exit 0 OPEN, 10 REVIEW_AGAIN, 20 NEEDS_DECISION, 30 MERGED; 1/2 mean the command failed |
| Old repo/issue after a CLI upgrade | `feature migrate` |

## Review round

1. **Track the PR.** Take the PR the caller named, else the current branch's open PR
   (`gh pr view --json number,headRefName,baseRefName,isDraft,state`). No PR → don't review;
   report that one must be opened against the parent branch. Merged → don't review; report
   `MERGED`. The **worktree** is the checkout that
   has its branch (`git worktree list`). If none has it, run `gh pr checkout <PR#>` inside a new
   `git worktree add --detach .claude/worktrees/<branch>` from the main checkout.
   - Untracked branch (`feature status` says so): `feature adopt`. It links the open PR and takes the PR's base as the parent, or refuses if
     the branch's git-town parent differs.
   - Tracked branch with no PR linked: `feature pr <PR#>`.
   - The PR's base isn't the tracked parent: don't review. Report the mismatch.
   - Unpushed commits in the worktree: push them first. The review reads the PR, not the disk.
   - Then `feature gate`. `OPEN`: don't review; report it, unless your arguments ask for a fresh
     review. `NEEDS_DECISION`: don't review either; the budget is spent or the loop churns, and only
     the user buys another round (`feature budget --set`, which reopens the gate). Report it.
2. **Describe the PR.** A branch from `feature start` got its title and description before any
   code existed. When they no longer fit what the branch does (always on its first ship), rewrite
   them. It runs in this order, with no headers:
   - The motivation, in one to three informal sentences, from the original motivation in the PR
     body and the commit messages (`git log --format='%s%n%b' origin/<base>..HEAD`), not from the
     diff: Sol's spec check reads the PR's intent from these sentences.
   - The evidence that it works, carried over from the existing description or the commits: a
     before and after, as a screencast or screenshots for a change users see, otherwise the test
     or command that failed before and passes now, with its output. Only what was run, and never
     a suite's pass count or a list of test names. A change with nothing to run or see (docs,
     comments) needs none.

   The title says what the PR does, in plain words. End the description with your signature
   line if your harness asks for one. `feature describe` takes the text with or without ship's
   block (editing the fetched body is fine) and writes exactly one, above that signature. Sol and
   step 6 read this description to know the PR's intent, so it comes first.
3. **Pick the depth**: `feature depth` prints the reasoning effort for this round. Before any
   review it comes from the diff stats and the paths touched; after one, from the last review's
   grade. Don't read the diff to second-guess it. Go higher only when your arguments ask for a
   deeper review (`ultra` only on request), and say so in the report.
4. **Have Sol find, blind.** Sol is the Codex provider's Sol model in T3. Load the T3 tools with
   ToolSearch, call `orchestrator_capabilities` with `search: "sol"`, and take the newest Sol
   model the `codex` provider lists. Then `delegate_task` with `mode: "wait"`, `role: "review"`,
   `timeoutMs: 3600000`, and
   `target: {providerInstanceId: "codex", model: <sol model>, options: {reasoningEffort: <depth>}}`.
   The `task` is the brief below, with the user's request filled in from the `Last prompt` line
   of `feature status`, and nothing about tracked problems or earlier rounds: Sol must find
   without them. If the wait times out, keep
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
   broken edge cases on realistic paths, races, missing error handling, and breakage outside
   the diff. Callers are the easy part of that. Follow what a symbol search misses: a wire
   format, a DB column or stored file another reader parses, a config key or feature flag,
   another language reading the same bytes, code a few hops downstream, the source of a library
   the change leans on. Check each against the code before reporting it. Skip style and taste.

   For each finding give: a title; severity (high = data loss, security hole, crash or silently
   wrong result on a realistic path; med = a real bug on a narrower path; low = anything else);
   file:line; a concrete failure scenario (inputs or state → wrong outcome); the evidence, as
   far up this ladder as is cheap: cited (you read the line), traced (you walked the failure
   step by step and it gets there), ran (a script or test calling the real code fails as
   described; include it); the root-cause fix. If you find nothing, say so plainly.

   Then, separately from the defects, two more checks, each under its own heading. Give each
   finding a title, file:line where there is one, and the root-cause fix; no severity.
   - Spec: what the PR's intent asks for that the diff leaves missing or partial, and behaviour
     the diff adds that nothing asked for. The intent is the PR description's prose (its evidence
     shows what was built, not what was asked), the issues it or its
     commits reference, and the user's request: <recorded request, or "none recorded">. Don't
     open issues labelled `feature` or `problem`: they're this review's own bookkeeping. Quote the
     line of intent each finding rests on. Something asked for but built wrong is a defect: report
     it with the defects above.
   - Design: breaches of the repo's documented coding rules (AGENTS.md, CLAUDE.md,
     CONTRIBUTING.md and the like, and your own instructions), citing the file and the rule; and
     these smells in the PR's own code, each a judgement call ("possible Feature Envy"):
     - Mysterious Name: a name that hides or misstates what the thing does or holds.
     - Duplicated Code: the same logic shape in more than one place in the change.
     - Feature Envy: a function that works on another object's data more than its own.
     - Data Clumps: the same few fields or parameters travelling together, a type wanting to exist.
     - Primitive Obsession: a string or number standing in for a domain concept.
     - Repeated Switches: the same switch or if-cascade on the same type in several places.
     - Shotgun Surgery: one logical change scattered as edits across many files.
     - Divergent Change: one module edited for several unrelated reasons.
     - Speculative Generality: abstractions, parameters or hooks for needs nothing has.
     - Message Chains: callers walking `a.b().c().d()` they shouldn't depend on.
     - Middle Man: a class or function that mostly just delegates onward.
     - Refused Bequest: a subclass or implementer that ignores most of what it inherits.
     A documented rule wins over a smell. Skip what a linter or formatter enforces.

   Then grade the PR as a whole: risk and complexity each low/med/high, the door and the blast
   radius, plus one or two sentences on what drives the risk and complexity:
   - risk, what a bug left in this change would cost: who it would reach (the blast radius) and
     how hard it would be to undo (the door). Judge the stakes from the repo (its README, what it ships and to whom): the same
     bug costs far less in a personal tooling repo than in a service with users. high: lasting
     harm to others that is hard to undo, like data lost or corrupted beyond easy restore,
     security or auth, money, migrations of stored data, shared infra or deploys. med: wrong
     behaviour that users or other systems hit on a main path, public APIs, error handling. low:
     a mistake its author sees and fixes by hand: docs, tests, tooling only its author runs,
     internal refactors with good coverage, and text written to GitHub (PR bodies, issues,
     comments), which keeps every edit. Risk is the cost of a bug, not how likely one is (that
     is complexity) or how serious the subject sounds.
   - door, one-way or two-way: whether reverting the PR undoes what a bug in it did. one-way:
     stored data rewritten or migrated, data or messages sent out, a deploy cutover or interface
     others come to depend on. two-way: a revert puts everything back.
   - blast radius, in a few words: who or what a bug would reach, e.g. "every vertical's page",
     "refine and chat titles", "only this repo's own tooling".
   - complexity, how hard the change is to get right. high: subtle state, ordering, caching, new
     invariants, many interacting parts. med: real logic in a few places. low: mechanical or local.
   ```
5. **Now load state**: `feature problem list --open`, plus closed problems for regression checks.
6. **Verify and dedup each finding** against the real code:
   - Take its evidence at least to traced, and to ran when a script or test against the real
     code is cheap. Stuck at cited → drop it.
   - Outside the PR's intent (other paths, rare cases, small perf) → `low`. So is code, a test or
     a guard the intent doesn't need.
   - Same meaning as an OPEN problem → skip.
   - Same as a CLOSED fixed problem → regression. `feature problem block <#> --reason
     "rediscovered: <repro>"`, and pass it in `--found` (and, if high/med, count it in `--regressions`).
     Never `gh issue reopen`.
   - Same as a WONTFIX problem (closed as not relevant; older ones are labelled `rejected`) → skip,
     unless the new repro answers the reason. Then `block` it. A problem disputed twice goes to the
     human: escalate with both sides.
   - Not caused by this PR → not a problem of this PR, so don't `problem add` it and it doesn't hold
     the gate. If it survives the same verification, list it under `Pre-existing` in the report.
   - Re-judge severity yourself, whatever Sol said: `high` = data loss, security hole, crash, or
     silently wrong result on a realistic path; `med` = real bug on a narrower path; `low` =
     everything else. Always pass `--sev`. Severity orders the work and feeds the churn trend; it
     doesn't decide what blocks. Every filed problem blocks until it's closed.
   - Spec and design findings go through the same dedup, then `--sev low`, always. They block like
     any other problem. Their evidence is a citation, not a trace: drop a spec gap whose quoted
     intent doesn't say what Sol claims, a rule breach the cited rule doesn't cover, and a smell
     outside the PR's own code. Title them `Spec: …` or `Design: …`.
7. **File new ones**: `feature problem add --title "…" --sev … --body-file - <<'EOF' … EOF`, with
   the repro, its evidence (traced or ran, with the script if ran; for a spec gap, the quoted
   intent; for a design finding, the rule or smell and the code), `file:line` and the suggested
   root-cause fix.
8. **Record**: `feature review record --sha $(git rev-parse HEAD) --found <#> … …`, with
   `--found` listing every problem steps 6–7 filed or blocked this round (bare `--found` if none);
   the CLI counts them by severity and lists them on the round's line in the PR body. Write `--summary` as a real
   sentence about what was reviewed and found, never a bare "clean pass"; name any spec and design
   findings in it. Pass the effort Sol ran
   at as `--depth`, and Sol's grade as `--risk`, `--door`, `--blast-radius`, `--complexity` and
   `--why`, never lowered. Raise complexity when the findings show the change was harder to get
   right than Sol graded. Raise risk only when a finding shows a bug costs more than Sol graded: it
   reaches further (widen the blast radius) or is harder to undo (the door turns one-way). Many findings in a low-risk change still mean low risk. The CLI writes the
   grade into the PR body and sizes the next round's depth from it: low risk always reviews at
   low.

   Recording holds the session (ship's Stop hook) until the gate settles: the caller fixes what
   this round filed before its turn can end.

## Rules

- Never lower a severity, or Sol's risk or complexity grade, to buy a cheaper review.
- Never ship debt. `wontfix` is only for a problem that isn't relevant (a false positive, by design,
  outside this PR's scope), never for a real problem nobody has fixed yet.
- Never fix, commit, close a feature out or merge here.
- Don't hand-edit the JSON block in a feature issue, or ship's block in a PR body.
- The CLI fails fast. Report its errors, don't work around them.

## Report

End with only this, filled in:

```
Feature: <name>  Branch: <branch>  PR: <url or none>  Worktree: <abs path>
Did: <one line>
Review: Sol <model> at <depth>; graded risk <…> (<door> door, blast radius <…>), complexity <…>; next round at <depth>   (or not run: <why>, with Sol's error verbatim if it failed)
Gate: <verdict from `feature review record` or `feature gate`, or not run>
Open: #<n> [sev] <title> — <file:line>, fix: <root-cause fix>   (every open problem, low included, one per line; or none)
Pre-existing: [sev] <title> — <file:line>, <failure scenario>, fix: <root-cause fix>   (bugs this PR didn't cause, one per line; or none)
Blocked: <only when the round couldn't run: no PR, the PR's base isn't the tracked parent, Sol unavailable or failed; what it needs>
```
