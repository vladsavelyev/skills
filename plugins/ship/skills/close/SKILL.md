---
name: close
description: Use when a PR has merged and its feature should be closed out, or the user says it's merged, done, or asks to close it out.
---

# close

Closing out records what shipped and closes the feature's tracking issue. It never reviews: once
the PR has merged a review can block nothing, so don't call `Skill(ship:ship)` for it. If the PR
hasn't merged, stop and say so; reviewing and the merge gate are `ship`'s job.

1. **Find the feature.** The branch is the PR's head branch
   (`gh pr view <PR#> --json headRefName,state,headRefOid`). `feature status --branch <branch>`
   works from any checkout of the repo. Not tracked: there's nothing to close, say so and stop
   (don't `feature adopt` a merged PR).
2. **Check the gate says `MERGED`.** Anything else means the PR hasn't merged: stop. If the status
   says it's already closed out, go to step 5.
3. **Decide the open problems.** Each problem still open that isn't a follow-up yet
   (`feature problem list --open --branch <branch>`) needs one of:
   - `feature problem follow-up <#> --reason "…" --branch <branch>`: worth fixing later; it stays
     open in the backlog.
   - `feature problem wontfix <#> --reason "…" --branch <branch>`: not worth fixing; closed as not
     planned.

   These are the user's decisions. Use any they already gave in this conversation; otherwise ask,
   with one recommendation and its reason for each, then apply them.
4. **Close out**: `feature merge --branch <branch>`. Never `--force`: it exists for features that
   shipped without their PR merging. The command writes what the gate still had outstanding (an
   unreviewed tail, blocking problems that shipped open) into the record; pass that on in the
   report.
5. **Tidy up.**
   - If the harness is watching the PR (T3's `watch_pull_request`), unwatch it.
   - The feature's worktree (`git worktree list`) is done with once it's clean and holds nothing
     the PR doesn't: `git -C <worktree> status --porcelain` is empty and its HEAD is the PR's
     `headRefOid`. Then leave it if your session is in it (`ExitWorktree`, `action: "keep"`), and
     run `git worktree remove <worktree>` and `git branch -D <branch>`. Keep the branch if another
     branch is stacked on it (`git config --get-regexp '^git-town-branch\..*\.parent$'` names it
     as a parent), and say so. A worktree with anything else in it stays, and the report names
     what is there.
6. **Report** in two or three lines: the feature closed, what shipped outstanding, the follow-ups
   kept, and what you removed or left.

`feature reconcile [--dry-run]` closes out every merged feature in the repo at once, for features
whose branch is long gone; the same problem decisions apply.
