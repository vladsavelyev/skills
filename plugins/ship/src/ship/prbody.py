"""Ship's block in a PR body: one tagged region the CLI owns, and the only part of the body it edits.

    <the author's description>

    <!-- ship -->
    > **Risk: low**: …
    > Complexity: low, size: S (2 files, 30 lines)
    > Feature tracking issue: closes #39
    > **Review round 1** (medium). Findings: none. A re-review after new commits would run at low.
    <!-- /ship -->

    🤖 <model> in <harness>

The CLI renders the whole quote from the feature's state (`assess.render`) and sets it between
the tags. Every body ship writes has exactly one block: written once, rewritten in place after
that. A body without one gets it above the author's signature (a trailing one-line paragraph
starting with `🤖`), or at the end if there is none. A new description may come with or without
the block (the natural round-trip fetches the body, edits it and passes it back): any block it
carries is dropped, since its content is always rendered from state, and one fresh block placed.

A block is any pair of tags on lines of their own; code blocks are not parsed. The accepted cost: a
description quoting the tags verbatim, each on a line of its own (a PR about ship), has that
example taken for ship's block and replaced. Quote them inline, indented or in a blockquote and
they are the author's text.
"""

import re

_BEGIN = "<!-- ship -->"
_END = "<!-- /ship -->"
# Tags count only on a line of their own: the legacy reference line below ends in the same
# `<!-- ship -->` text. A trailing `\r` is part of the line end: a body edited in GitHub's web UI
# has `\r\n`. The block was `ship:assessment` before it held the reference too; PR bodies on GitHub
# carry that name, so it is read as the block (and renamed on write) ON PURPOSE.
_BLOCK_RE = re.compile(r"(?ms)^<!-- ship(?::assessment)? -->[ \t]*\r?$.*?^<!-- /ship(?::assessment)? -->[ \t]*(?=\r?$)")
_SIGNATURE_PREFIX = "🤖"


# Before the block held it, the reference was a line of its own, marked so it could be told from
# the author's own `Closes #…` lines; before that, the non-closing `Part of #<issue>`. Kept ON
# PURPOSE, not cruft: PR bodies on GitHub carry these, and the first write under the current tags
# drops them for this feature's issue, since the block now carries the reference. Lines naming
# another issue are the author's (an example quoting ship's format) and stay.
def _legacy_ref_re(issue: int) -> re.Pattern:
    return re.compile(rf"(?m)\n*^(?:Closes #{issue} <!-- (?:ship|feature-workflow) -->|Part of #{issue})[ \t]*$")


def _blocks(body: str) -> list[re.Match]:
    return list(_BLOCK_RE.finditer(body))


def _without(body: str, start: int, end: int) -> str:
    """`body` with `body[start:end]` cut out, and the paragraphs either side joined by one blank line."""
    head, tail = body[:start].rstrip("\r\n"), body[end:].lstrip("\r\n")
    return f"{head}\n\n{tail}" if head and tail else head or tail


def _wrap(quote: str) -> str:
    return f"{_BEGIN}\n{quote.strip()}\n{_END}"


def _place(text: str, block: str) -> str:
    """`text` with `block` added above its trailing signature (a one-line paragraph starting with
    `🤖`, so the end of a code block quoting one never counts), or at the end."""
    text = text.rstrip()
    head, _, last = text.rpartition("\n\n")
    if last.startswith(_SIGNATURE_PREFIX) and "\n" not in last:
        return f"{head}\n\n{block}\n\n{last}\n" if head else f"{block}\n\n{last}\n"
    return f"{text}\n\n{block}\n" if text else f"{block}\n"


def _one_block(body: str, block: str) -> str:
    """`body`, once checked to hold exactly one block and that it is `block`, the one just written:
    the invariant of every body ship writes. A stray opening tag left in the author's text (half a
    quoted example) breaks it: the block would then run from that tag, swallowing the text between."""
    found = [m.group(0) for m in _blocks(body)]
    if found != [block]:
        raise ValueError(
            f"Refusing to write a PR body with {len(found)} ship blocks, not the one just rendered. "
            f"Does the description have a {_BEGIN} or {_END} line of its own without its pair?"
        )
    return body


def with_block(body: str, quote: str, issue: int) -> str:
    """`body` with ship's block set to `quote`, rewriting only what is between its tags.

    A body that has no block under the current tags yet is one ship wrote before: its old
    reference lines for `issue` are dropped, since the block carries the reference now, and a
    block under the old name is replaced. A body with several blocks (written before ship kept to
    one, or edited by hand) keeps the last one's place and drops the others. After that nothing
    outside the tags changes."""
    blocks = _blocks(body)
    if not blocks or not blocks[-1].group(0).startswith(_BEGIN):
        body = _legacy_ref_re(issue).sub("", body)
        blocks = _blocks(body)
    block = _wrap(quote)
    if not blocks:
        return _one_block(_place(body, block), block)
    *extra, last = blocks
    body = body[: last.start()] + block + body[last.end() :]
    for m in reversed(extra):  # right to left, so the spans still to cut stay where they were
        body = _without(body, m.start(), m.end())
    return _one_block(body, block)


def with_description(text: str, quote: str) -> str:
    """`text` as the new description, with ship's block placed below it. Any block `text` carries
    (it is often the PR body fetched, edited and passed back) is dropped first: nothing in it is
    the author's, since it is rendered from state."""
    for m in reversed(_blocks(text)):
        text = _without(text, m.start(), m.end())
    block = _wrap(quote)
    return _one_block(_place(text, block), block)
