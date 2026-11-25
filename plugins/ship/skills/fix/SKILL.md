---
name: fix
description: Use when `Skill(ship:ship)` fixes the open problems of its run.
context: fork
background: false
---

# fix

You run in a forked subagent and close the open problems of a ship run: fix each at its root cause,
or close it as `wontfix` if it isn't relevant. Then end with the **report** below: the caller,
`Skill(ship:ship)`, sees only that.

Your arguments name the feature worktree and the PR, and may name the problems to take (else every
open one: `feature problem list --open`), a merge conflict `feature sync` stopped at, and what the
caller knows that the code and the issues don't say: what the user asked of the change, a
constraint they set, a fix they turned down.

**Never ship debt.** Every problem, at every severity including `low`, is fixed or, if it isn't
relevant, closed as `wontfix` with the reason.

**Work in the feature worktree.** The main checkout has the same files on the base branch. Prefix
every command with `cd <worktree> &&`, and give Read, Edit and Write absolute paths under the
worktree.

## A merge conflict

Resolve it first, keeping what each side meant, then `git add` the files, `git town continue`, which
finishes the sync and pushes, and `feature sync` again: only it records the merge for the review.

## Each problem

1. Fix it at its root cause, not the symptom. Prefer deleting or narrowing over new code. Show the
   fix works: the repro or a test that fails before it and passes after.
   Never run `sudo`, not even `sudo -n`: it stops for the user's approval. To make a file owned by
   root or by a uid the host doesn't know, chown it from a container (`docker run --rm -v <dir>:/d
   alpine chown -R <uid>:<gid> /d/<path>`), and hand it back the same way once done, so the scratch
   dir can be deleted.
2. Commit the fix apart from other work (a commit that fixes only `low` problems needs no review),
   push, then `feature problem resolve <#> --commit <sha>…` with every commit of the fix.

Not relevant (wrong, by design, out of this PR's scope): `feature problem wontfix <#> --reason
"<evidence>"`, changing no code. A choice only the user can make, which neither the code nor their
request settles: leave it open, and put the question, its options and your recommendation in the
report. So is a `wontfix` a reviewer disputed (the problem's last comment), when the evidence still
says it isn't relevant: give both sides.

Before the report, wait for every test run you started, and leave every fix committed and pushed.
Uncommitted changes that were there before you started aren't yours: leave them, and say so.

## Rules

- Never review, escalate, release, merge, post on the PR, or change the budget: those are the
  caller's.
- Never lower a severity to make a problem cheaper to close.
- The CLI fails fast. Report its errors, don't work around them.

## Report

End with only this block, one line per problem, each as short as it can be and still say what the
caller passes on to the user:

```
Fixed: #<n> <title> — <sha>…: <the root cause, and what shows the fix works>
Won't fix: #<n> <title> — <why it isn't relevant>
Question: #<n> <title> — <the question>; options: <…>; recommend: <…>
Not done: #<n> <title> — <what stopped you>
Conflict: resolved in <sha> | <what stopped you>   (only when the caller named one)
Worktree: clean | <what's left uncommitted, and whose>
```
