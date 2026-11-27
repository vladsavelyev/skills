---
name: review
description: Use when `Skill(ship:ship)` runs a review round of a PR, or the user asks for a single review round without fixing.
context: fork
background: false
---

# review

You run in a forked subagent and do one review round of a PR: two reviewers find, blind; you verify, dedup
and file what they found, and record the round. Then end with the **report** below: the caller,
usually `Skill(ship:ship)`, sees only that. Your arguments name the PR (else take the current
branch's) and anything the user asked of this round (a fresh review past an OPEN
gate, another reviewer).

**Never ship debt.** Every problem a review files, at every severity including `low`, is closed
before the PR ships: fixed, or closed as `wontfix` because it isn't relevant. The gate stays shut
while any problem is open, so file every real finding: the caller fixes them all.

The `feature` CLI keeps all state in GitHub and git config: a tracked feature is a branch, its
worktree, its PR and a tracking issue, and review findings are its sub-issues. A merged PR is
`Skill(ship:close)`'s: if the PR has merged, or the gate says `MERGED`, do nothing else and report
`MERGED`.

**Work in the feature worktree.** The main checkout has the same files on the base branch. Prefix
every command with `cd <worktree> &&`, and never read from the main checkout or another worktree.

## Commands

| Situation | Command |
| --- | --- |
| Track the branch | `feature adopt` (also creates the workflow labels if the repo lacks them) |
| Resume | `feature status` |
| Record the user's request | `feature prompt "<request>"` |
| Link the PR | `feature pr <number>` |
| Rewrite the PR description | `feature describe [--title "<t>"] [--attach <file>]… --body-file - <<'EOF' … EOF` |
| The two reviewers | `feature reviewer` |
| Options for a reviewer's effort | `feature effort --model-options '<json>'` |
| Record a review | `feature review record --sha <sha> --found [<problem#> ...] [--regressions <problem#> ...] --summary "<sentence>" --risk <r> --door <one-way\|two-way> --blast-radius "<words>" --complexity <c> --why "<sentence>"` |
| Problems | `feature problem list [--open]`, `add [--repeats <fixed #>]`, `block <#> --reason "…"` |
| Merge check | `feature gate`: exit 0 OPEN, 10 REVIEW_AGAIN, 20 NEEDS_DECISION, 30 MERGED, 40 REDESIGN; 1/2 mean the command failed |
| Old repo/issue after a CLI upgrade | `feature migrate` |

## Review round

1. **Track the PR.** Take the PR the caller named, else the current branch's open PR
   (`gh pr view --json number,headRefName,baseRefName,isDraft,state`). No PR → don't review;
   report that one must be opened against the parent branch. The **worktree** is the checkout that
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
     `REDESIGN`: don't review either; a kind of bug came back after its fix, and the caller's fix
     round redesigns first, so a review now would read code about to be replaced. Report it.
2. **Describe the PR.** A branch from `feature start` got its title and description before any
   code existed. When they no longer fit what the branch does (always on its first ship), rewrite
   them. It runs in this order, with no headers:
   - The motivation, in one to three informal sentences, from the original motivation in the PR
     body and the commit messages (`git log --format='%s%n%b' origin/<base>..HEAD`), not from the
     diff: the reviewer's spec check reads the PR's intent from these sentences.
   - The evidence that it works, carried over from the existing description or the commits, laid
     out as "Prove the PR works" in your instructions says. Keep its attachments' links as they
     are: they already point at GitHub.

   The title says what the PR does, in plain words. End the description with your signature
   line if your harness asks for one. `feature describe` takes the text with or without ship's
   block (editing the fetched body is fine) and writes exactly one, above that signature. The
   reviewer and step 5 read this description to know the PR's intent, so it comes first.
3. **Have both reviewers find, blind, side by side.** Every round runs two, and `feature
   reviewer` prints a T3 provider instance and a model family for each:
   - **the reviewer**, with the brief below: the one your arguments name ("review with Gemini"),
     else the default. A name replaces only this reviewer: the `/code-review` reviewer runs
     every round, so naming Claude makes both reviewers Claude;
   - **the `/code-review` reviewer**, running Claude Code's `/code-review` as `feature reviewer`
     prints it, with the task after the brief.

   Load the T3 tools with ToolSearch and call `orchestrator_capabilities` once, for both: take
   for each the provider that offers its family and the newest model of that family listed. Only
   a family missing from that list gets a second call, with `search: "<the name or family>"`.
   `feature effort --model-options '<that model's options array, as listed>'` prints the
   `delegate_task` options that run it at high reasoning effort, the effort of every round: never
   set another, whatever the PR. Start both in one message, each a `delegate_task` with `mode:
   "wait"`, `role: "review"`, `timeoutMs: 45000` (the most the tool takes; a timeout doesn't
   cancel the child), and `target: {providerInstanceId: "<provider>", model: <model>, options:
   <its printed options>}`. Keep both task ids.
   Both tasks carry nothing about tracked problems or earlier rounds: the reviewers must find
   without them. Fill the user's request in the brief from the `Last prompt` line of `feature
   status`. A review runs longer than the wait: when one comes back unfinished, call
   `t3_thread_wait` on its child thread, and again each time that returns unfinished, until both
   runs finish, even where a result says to end your turn. You run as a subagent, so ending your
   turn ends the round without a review. Never cancel a running reviewer to review yourself
   instead. The round fails, and you stop, when no model of a family is listed, `feature effort`
   refuses it, either task comes back failed (an auth or gateway error, a crash, or the
   `/code-review` reviewer's "review failed"), or either waits on a question or an approval:
   nobody here answers it. Then `task_cancel` the other if it's still running, record no review,
   put the failing reviewer's error or question verbatim in the report, and don't substitute
   another reviewer or record the round on one reviewer's findings.

   The reviewer's brief:
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

   Then, separately from the defects, three more checks, each under its own heading. Give each
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
   - Comments: each comment, docstring and lint or type-checker suppression the diff adds or
     rewrites. A comment that explains our own code is usually covering for it, so read each one
     as a pointer to the code and sort it:
     - Covers a flaw: it explains a surprise in our own code (a misleading name, an argument that
       must be passed just so, an order that matters, a special case), or justifies a workaround
       ("fine for now", "too risky to change", "needed because X does Y"). The finding is the
       code, named by its symbol, and the fix is the redesign that makes the comment unnecessary:
       a rename, an extracted function, a type, the workaround removed at its cause. The comment
       goes with it. A shorter comment is never the fix.
     - Claims a constraint: "do not remove", "keep in sync with X", "must run before Y". Check the
       claim against the code. If it holds, the fix enforces it with a type, test, runtime check
       or lint, and deletes the comment; if it doesn't, the fix deletes the comment.
     - Says nothing the code doesn't: narration of what it does, banners, commented-out code,
       history ("was X", "now uses Y"). The fix is to delete it.
     - Kept, and no finding:
       - license headers and formatter directives (`# fmt: off`, `// prettier-ignore`);
       - behaviour forced by an external dependency, platform, vendor or protocol we can't
         change, checked against the code: a surprise in our own code doesn't qualify;
       - doc comments and docstrings that define a public API's contract;
       - issue or RFC links that explain a constraint the code can't express;
       - comments the repo's AGENTS.md or CLAUDE.md asks for, by that file's own bar;
       - text the code reads at runtime, where cutting it changes behaviour: a docstring that
         becomes a schema or a prompt (a pydantic model's does), a tool description, CLI help.
     Report a comment when you're unsure a keep clause covers it, unless cutting it could change
     behaviour. A suppression stays only when its rule is wrong here or style-only; one that
     silences a correctness or safety rule covers a flaw, fixed in the code it silences.

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
   The `/code-review` reviewer's task, with the command `feature reviewer` printed, given PR
   #<n>'s URL (`https://github.com/<owner/repo>/pull/<n>`). By URL, `/code-review` stops when it
   can't read the PR's diff, rather than review the checkout's own diff against the default branch.
   ```
   Run `<that command>` on PR #<n> in <owner/repo>. The PR's branch is checked out at
   <worktree abs path>; read code only there, never another checkout. Read-only: no edits,
   commits, pushes or GitHub comments, and don't pass --comment or --fix. When it finishes,
   write every finding it reported in your final message as plain text, one per finding: title,
   severity, file:line, failure scenario, evidence and fix. If it reported none, say so.
   If it says nothing was reviewed, report the review failed, quoting its sentence.
   ```
4. **Now load state**: `feature problem list --open`, plus closed problems for regression checks.
5. **Verify and dedup each finding** of both reviewers against the real code:
   - The same defect from both reviewers is one finding: merge them, keeping the better evidence
     and failure scenario.
   - Take its evidence at least to traced, and to ran when a script or test against the real
     code is cheap. Stuck at cited → drop it.
   - Never run `sudo`, not even `sudo -n`: it stops for the user's approval. To make a file owned by
     root or by a uid the host doesn't know, chown it from a container (`docker run --rm -v <dir>:/d
     alpine chown -R <uid>:<gid> /d/<path>`), and hand it back the same way once done, so the
     scratch dir can be deleted.
   - Outside the PR's intent (other paths, rare cases, small perf) → `low`. So is code, a test or
     a guard the intent doesn't need.
   - Same meaning as an OPEN problem → skip.
   - Same as a CLOSED fixed problem → regression. `feature problem block <#> --reason
     "rediscovered: <repro>"`, and pass it in `--found` (and, if high/med, in `--regressions`).
     Never `gh issue reopen`.
   - A new bug, but the same mistake as a CLOSED fixed problem in a new place: a fix at the root of
     that problem's cause would have prevented this one too → file it with `--repeats <#>`. It tells
     the gate the fix patched an instance, not the cause. A shared category isn't enough: two
     unrelated missing null checks don't repeat each other.
   - Same as a WONTFIX problem (closed as not relevant; older ones are labelled `rejected`) → skip,
     unless the new repro answers the reason. Then `block` it. A problem disputed twice goes to the
     human: escalate with both sides.
   - Not caused by this PR → not a problem of this PR, so don't `problem add` it and it doesn't hold
     the gate. If it survives the same verification, list it under `Pre-existing` in the report.
   - Re-judge severity yourself, whatever a reviewer said: `high` = data loss, security hole, crash, or
     silently wrong result on a realistic path; `med` = real bug on a narrower path; `low` =
     everything else. Always pass `--sev`. Severity orders the work and feeds the churn trend; it
     doesn't decide what blocks. Every filed problem blocks until it's closed.
   - Spec, design and comment findings (the `/code-review` reviewer's broken-rule and cleanup findings are design findings) go through the same dedup, then `--sev low`, always. They
     block like any other problem. Their evidence is a citation, not a trace: drop a spec gap whose
     quoted intent doesn't say what the reviewer claims, a rule breach the cited rule doesn't cover, a smell
     outside the PR's own code, a comment the PR didn't add or rewrite, and one the brief's keep
     list covers (read the code for the external constraint or the runtime read it claims). Title
     them `Spec: …`, `Design: …` or `Comments: …`. A comment that covers a flaw is a design
     finding: file it as `Design: …` on the code it names, with the comment quoted in the body, and
     merge it with any smell or rule finding on the same code. If the reviewer proposed only deleting a
     comment that explains our own code or justifies a workaround, file the flaw it covers
     instead, with its redesign. A constraint to enforce is a `Comments: …` problem of its own.
     The comments that only need deleting go in one `Comments: …` problem, listing each
     `file:line`.
6. **File new ones**: `feature problem add --title "…" --sev … [--repeats <#>] --body-file - <<'EOF' … EOF`, with
   the repro, its evidence (traced or ran, with the script if ran; for a spec gap, the quoted
   intent; for a design finding, the rule or smell and the code; for a comment, the comment, which kind
   it is and why no keep clause covers it), `file:line` and the suggested
   root-cause fix.
7. **Record**: `feature review record --sha $(git rev-parse HEAD) --found <#> … …`, with
   `--found` listing every problem steps 5–6 filed or blocked this round (bare `--found` if none);
   the CLI counts them by severity and lists them on the round's line in the PR body, which shows
   once they're all closed. Write
   `--summary` as a real sentence about what was reviewed and found, never a bare "clean pass";
   name any spec, design and comment findings in it. Pass the reviewer's grade as `--risk`,
   `--door`, `--blast-radius`, `--complexity` and `--why`, never lowered. Raise complexity when
   the findings show the change was harder to get right than the reviewer graded. Raise risk only
   when a finding shows a bug costs more than it graded: it reaches further (widen the blast
   radius) or is harder to undo (the door turns one-way). Many findings in a low-risk change still
   mean low risk. The CLI writes the grade into the PR body for the human.

## Rules

- Never lower a severity, or the reviewer's risk or complexity grade.
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
Review: <provider> <model> and <provider> <model> (/code-review); graded risk <…> (<door> door, blast radius <…>), complexity <…>   (or not run: <why>, with the failing reviewer's error verbatim if it failed)
Gate: <the gate line from `feature review record` or `feature gate`, its reasons included, or not run>
Open: #<n> [sev] <title> — <file:line>, fix: <root-cause fix>   (every open problem, low included, one per line; or none)
Pre-existing: [sev] <title> — <file:line>, <failure scenario>, fix: <root-cause fix>   (bugs this PR didn't cause, one per line; or none)
Blocked: <only when the round couldn't run: no PR, the PR's base isn't the tracked parent, a reviewer unavailable or failed; what it needs>
```
