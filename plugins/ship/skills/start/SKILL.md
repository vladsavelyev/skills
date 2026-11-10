---
name: start
description: Use when the user starts new work — a new feature, branch, worktree or draft PR.
---

# start

A feature starts as a branch, a worktree and a draft PR, nothing more. Review tracking comes
later, when the user asks to ship it (`Skill(ship:ship)`).

1. **Name it** short and plain: `snowflake-nl2sql`, not `feature/snowflake-nl2sql-instructions`.
2. **Write the PR description**: the motivation in one to three informal sentences, from what the
   user asked for. No "Summary" header, no test plan. `ship` rewrites it once the code exists.
3. **Start it**, from anywhere in the repo:
   ```
   feature start <name> --body "<motivation>" [--base <parent-branch>] [--title "<PR title>"]
   ```
   `--base` stacks it on another feature's branch; the default is `main`. If it refuses, its
   message says what to do.
4. **Move in**: `EnterWorktree` with `path: <worktree>` from the output (never `name:`). Editing
   from the main checkout edits the base branch.
5. Link the draft PR in T3 if the harness offers it (`link_pull_request`).

Then do the work, committing and pushing as you go.
