---
name: babysit-pr
description: Watch a pull request through CI and review until it's ready, fixing or dismissing what comes up. Use when the user asks to monitor, watch, or babysit a PR.
---

# Babysit a PR

Babysitting means taking a PR from "pushed" to "ready to merge": CI green, every review finding
handled, and current with its base branch. Many repos have AI review bots. Their findings are often
useful and sometimes wrong, so check each one.

The **base branch** is the branch the PR merges into
(`gh pr view <pr> --json baseRefName`). On a stacked PR that is the parent feature branch, not
`main`. Every "keep up with the base" step below means that branch.

On `vladsavelyev/*` repos, run every `gh` and `git push` with
`GH_TOKEN=$(gh auth token --user vladsavelyev)` in front.

## Waiting

Wait with T3 Code's `watch_pull_request`, one watch per PR. Never write a watcher, polling loop or
`sleep` loop of your own. Every watch on a GitHub account draws on the same rate limit.

1. Handle what is already there (failed checks, open comments, conflicts) first.
2. Call `watch_pull_request` and end your turn. T3 Code wakes you when a check fails, the required
   checks pass, someone comments or reviews, or the branch conflicts.
3. When a wake needs no action, end the turn without a message. Never say or post "still waiting".

Rules for the watch:

- **Only the thread that owns the PR watches it.** A delegated child (a reviewer, any
  `delegate_task` child) never calls `watch_pull_request`; the parent owns the PR. Don't watch PRs
  you aren't working on.
- **A wake is news, not a verdict.** Check the merge gate below yourself.
- **Unwatch before you hand back.** A watched thread stays in Working and out of the user's inbox.
  When you stop — ready, blocked, or asking the user something — call `unwatch_pull_request`
  first. A merge or close ends the watch by itself.
- **"Stopped watching, could not read it"** usually means the GraphQL rate limit is spent. Check
  `gh api rate_limit --jq .resources.graphql`. If `remaining` is near 0, wait for `reset` with one
  background command, then watch again. Never re-watch in a tight loop.

## GitHub rate limits

`gh pr create`, `gh pr view` and `gh pr checks` use GraphQL. REST has a separate limit that usually
still has room when GraphQL is spent: read the PR with `gh api repos/<owner>/<repo>/pulls/<n>` and
its checks with `gh api repos/<owner>/<repo>/commits/<sha>/check-runs`.

## Reviews and CI

- Act only on checks and comments newer than the latest push.
- Verify every bot finding against the source before changing code. Fix the real ones.
- For a failed check, tell a failure in the repo's code apart from an infrastructure flake. Re-run
  a flake; fix a real failure.
- For a finding not worth addressing, reply with the reason and resolve the thread. Post every
  comment and reply with the `pr-comments:pr-comments` skill.
- If the repo has `.macroscope/check-run-agents/*.md`, read those rules before replying to a
  Macroscope finding; its findings cite them.
- CodeRabbit is advisory: treat its findings like any other, but never wait for it or hold a merge
  for it.
- Screenshots or videos help reviewers when you have a way to upload them.

Keep the PR the size the user's goal needs. Review loops grow a PR one cheap-looking fix at a time.
Fix real bugs in the intended case. Decline the rest with a written reason and list them as
follow-ups: tests nobody needs, guards for edge cases that don't matter, extra compatibility, small
performance costs. Prefer the fix that deletes or narrows code.

## Base branch and merging

`watch_pull_request` reports conflicts but not a PR falling behind, so check that yourself before
calling the PR ready: `gh pr view <pr> --json mergeStateStatus` shows `BEHIND` or `DIRTY`, and
`git log HEAD..origin/<base>` shows what landed on the base. Update the branch with `git town sync`
(it merges the base in, so no force-push). If the repo is set to rebase instead, ask before
force-pushing.

On a stack:

- When the parent PR merges, `git town sync` moves this branch onto the parent's own base. Check
  the PR's base on GitHub afterwards and fix it with `gh pr edit <pr> --base <branch>` if it still
  names the merged branch.
- Merge bottom-up. A PR whose base is another feature branch merges into that branch, not into
  the repo's trunk, so don't merge it before its parent unless the user says so.

If an overlapping PR makes this one obsolete, stop watching, tell the user, and ask before closing
it unless closing was already authorized.

The PR is **ready** when, on the latest head:

- the required checks pass, and the review bots the repo uses have reviewed that head;
- no review thread is unresolved;
- it is not `BEHIND` or `DIRTY`, and nothing that landed on the base since the last green run
  touches the PR's files;
- on a branch tracked by ship, `feature gate` passes (see `ship:ship`).

Stop there and report it ready. Merge only when the user explicitly asked for a merge. Right before
merging, `git fetch` and check the gate again; if the base moved, sync, push, and wait for green
again.
