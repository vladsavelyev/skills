---
name: pr-comments
description: Use whenever posting a comment on GitHub on the user's behalf — PR and issue comments, inline review comments, review summaries, replies to reviewers or bots.
---

# Comments on GitHub

A comment you post on GitHub shows up under the user's account, so readers must be able to tell an
agent wrote it. Open every such comment with this callout:

```md
> [!NOTE]
> 🤖 **<model> on behalf of <login>**

The comment itself starts here.
```

- `<model>`: the public name of the model writing the comment (e.g. "Claude Opus 5.5"), as the
  harness states it. If it doesn't state one, use the harness name. Never guess.
- `<login>`: the GitHub login the comment is posted as (`vladsavelyev` on personal repos,
  `kbww511_azu` on work ones), without `@` — a mention would notify them.
- Only the attribution goes in the callout. The comment follows after one blank line, with no
  heading, horizontal rule, sign-off, or second attribution.

This applies to top-level PR and issue comments, inline review comments, review summaries,
replies to reviewers and bots, and the explanation left before resolving or dismissing a thread.
It doesn't apply to PR or issue descriptions, commit messages, or text the user asked you to post
verbatim.

## Writing it

- Lead with the conclusion.
- Point at the evidence: `file:line`, the failing check, a repro, the commit.
- Say what you verified and what is your judgment.
- No thanks, filler, or narration of what you did.

## Before posting

- Read the whole comment, quotes and attachments included, for secrets (tokens, keys, passwords)
  and private details. On a public repo that includes anything from work: internal hostnames,
  repo names, data, people.
- Write the body to a file and post it with `--body-file` (or `-F body=@file` for `gh api`), so
  the shell doesn't mangle quotes, backticks or blank lines. Re-read that file as rendered
  Markdown: the callout, the blank line after it, code blocks.
- Post as the right account: on `vladsavelyev/*` repos, prefix the command with
  `GH_TOKEN=$(gh auth token --user vladsavelyev)`.

## After posting

Confirm GitHub returned the comment or review URL. Resolve a review thread only after your reply
to it was posted.
