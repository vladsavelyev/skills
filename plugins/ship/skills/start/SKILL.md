---
name: start
description: Start a new feature — a short-named branch off main (or off a parent branch, to stack), its own worktree, and a draft PR. Use when the user begins new work or asks for a new branch, feature or worktree.
---

# start

A feature starts as a branch, a worktree and a draft PR, nothing more. Review tracking comes
later, when the user asks to ship it (`Skill(ship:ship)`).

1. **Name it** short and plain: `snowflake-nl2sql`, not `feature/snowflake-nl2sql-instructions`.
2. **Write the PR description**: the motivation in one to three informal sentences, from what the
   user asked for. No "Summary" header, no test plan.
3. **Start it**, from anywhere in the repo:
   ```
   feature start <name> --body "<motivation>" [--base <parent-branch>] [--title "<PR title>"]
   ```
   `--base` stacks it on another feature's branch; the default is `main`. It branches off the
   freshly fetched remote base, without touching the main checkout, then makes an empty first
   commit, pushes, and opens the draft PR against the base. It refuses a local base with
   unpushed commits, which would leak into the PR (`--allow-local-base` overrides when that's
   deliberate). For a `vladsavelyev/*` repo, prefix the command with
   `GH_TOKEN=$(gh auth token --user vladsavelyev)`.
4. **Move in**: `EnterWorktree` with `path: <worktree>` from the output (never `name:`). Editing
   from the main checkout edits the base branch.
5. Link the draft PR in T3 if the harness offers it (`link_pull_request`).

Then do the work, committing and pushing as you go. When the user asks to ship, review, or check
whether the feature can merge, call `Skill(ship:ship)`.
