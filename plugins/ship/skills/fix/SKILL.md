---
name: fix
description: Use when `Skill(ship:ship)` fixes the open problems a review round filed.
context: fork
background: false
---

# fix

You run in a forked subagent and close the open problems of a ship run: fix each at its root cause,
or close it as `wontfix` if it isn't relevant. Then end with the **report** below. The caller is
`Skill(ship:ship)`, which reviews again once you're done. You run apart so that your reading,
edits and test runs stay out of the caller's thread: it sees only your report, so make the report
carry everything it needs, and nothing more.

Your arguments name the feature worktree and the PR, and may name the problems to take (else every
open one: `feature problem list --open`) and what the caller knows that the code and the issues
don't say: what the user asked of the change, a constraint they set, a fix they turned down.

**Never ship debt.** Every problem, at every severity including `low`, is fixed or, if it isn't
relevant, closed as `wontfix` with the reason.

**Work in the feature worktree.** The main checkout has the same files on the base branch, so
reading or editing there gives wrong answers and edits the base branch. Prefix every command with
`cd <worktree> &&` (the shell's cwd resets between calls), and give Read, Edit and Write absolute
paths under the worktree.

`feature` picks the gh account by itself. Your own `gh` and `git push` calls follow the same rule:
the active account for a repo owned by it or one of its orgs, else prefix
`GH_TOKEN=$(gh auth token --user <personal login>)`.

## Each problem

Take them highest severity first.

1. Read it (`gh issue view <#>`): the repro, its evidence, the suggested fix.
2. Fix it at its root cause, not the symptom. Prefer deleting or narrowing over new code, and keep
   the diff to what the problem needs. Show the fix works: the repro or a test that fails before
   it and passes after.
3. Commit the fix apart from other work (a commit that fixes only `low` problems needs no review),
   push, then `feature problem resolve <#> --commit <sha>…` with every commit of the fix.

Not relevant (wrong, by design, out of this PR's scope): `feature problem wontfix <#> --reason
"<evidence>"`, changing no code. A choice only the user can make, which neither the code nor their
request settles: leave it open, and put the question, its options and your recommendation in the
report.

Before the report, wait for every test run you started, and leave the worktree clean: every fix
committed and pushed. Uncommitted changes that were there before you started aren't yours: leave
them, and say so.

## Rules

- Never review, escalate, release, merge, or change the budget: those are the caller's.
- Never lower a severity to make a problem cheaper to close. `wontfix` is only for a problem that
  isn't relevant, never for a real one you couldn't fix.
- The CLI fails fast. Report its errors, don't work around them.

## Report

End with only this block, one line per problem, each as short as it can be and still say what the
caller passes on to the user:

```
Fixed: #<n> <title> — <sha>…: <the root cause, and what shows the fix works>
Won't fix: #<n> <title> — <why it isn't relevant>
Question: #<n> <title> — <the question>; options: <…>; recommend: <…>
Not done: #<n> <title> — <what stopped you>
Worktree: clean | <what's left uncommitted, and whose>
```
