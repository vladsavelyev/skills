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
3. **Decide the open problems.** A PR merged past the gate can leave problems open
   (`feature problem list --open --branch <branch>`), and `feature merge` refuses until each has a
   decision. One the list marks `handed-off` is decided already: leave it. Ask the user, for each
   of the others:
   - fix it in a separate PR: `feature problem hand-off <#> --reason "fix in a separate PR" --branch
     <branch>`, then, after closing out, start that PR (`Skill(ship:start)` off the base branch)
     with `Closes #<problem>` in its description;
   - or just keep it as an issue: `feature problem hand-off <#> --reason "kept as an issue: …"
     --branch <branch>`.

   Either way it stays open, labelled `handed-off`, outside the feature. A problem that isn't
   relevant (and for a PR closed unmerged, whose problems never landed) closes instead: `feature
   problem wontfix <#> --reason "…" --branch <branch>`. Use decisions the user already gave in this
   conversation; otherwise ask, with one recommendation and its reason for each, then apply them.
4. **Close out**: `feature merge --branch <branch>`. Never `--force`: it exists for features that
   shipped without their PR merging. The command writes what the gate still had outstanding (an
   unreviewed tail, the problems handed off) into the record; pass that on in the report.
5. **Tidy up.**
   - If the harness is watching the PR (T3's `watch_pull_request`), unwatch it.
   - If your session is in the feature's checkout, leave it (`ExitWorktree`, `action: "keep"`).
   - `feature tidy --branch <branch>` removes the checkout and the local branch, once they hold
     nothing the PR doesn't. It removes nothing while the checkout holds any of these, and lists
     them:
     - a modified or untracked file;
     - a HEAD that isn't the PR's head, or a file assume-unchanged or skip-worktree hides from
       `status`;
     - a repository inside it: another worktree, a submodule, a clone;
     - an ignored path the main checkout doesn't have and that isn't a rebuildable cache;
     - a folder you can't empty.

     Ignored files that running the project makes (`.env`, a local config, logs: the main
     checkout has them too) and caches (`.venv`, `node_modules`) go without asking. A
     branch another is stacked on stays; tidy says so.

     When it refuses, act on what it lists: commit and push work, or ask the user about an
     ignored path, with your recommendation, and pass each one they OK deleting as
     `--accept <path>`. A folder you can't empty was made by something running as root, a
     container usually: fix where it's made so it's made as the user, and never reach for sudo.
     When the branch is checked out in the main checkout, tidy leaves the checkout: check out the
     base branch there, pull it, then run `feature tidy --branch <branch>` again, which deletes the
     branch.
6. **Report** in two or three lines: the feature closed, what shipped outstanding, what happened to
   each open problem (handed off to a fix PR, kept as an issue, or wontfix), any fix PR you
   started, and what you removed or left.

`feature reconcile [--dry-run]` closes out every merged feature in the repo at once, for features
whose branch is long gone; it refuses the same way while a feature has an undecided problem.
