"""Ship's block in a PR body: one tagged region the CLI owns, and the only part of the body it edits.

    <the author's description, its sketch and its ## Evidence>

    <!-- ship -->
    ## Risk

    **Low**, two-way door: …
    **Blast radius:** …
    **Complexity:** low, size S (2 files, 30 lines)

    > Feature tracking issue: closes #39
    > **Review round 1** (medium). Findings: none. A re-review after new commits would run at low.
    <!-- /ship -->

    🤖 <model> in <harness>

The CLI renders the block's whole content from the feature's state (`assess.render`) and sets it between
the tags. Every body ship writes has exactly one block: written once, rewritten in place after
that. A body without one gets it above the author's signature (a trailing one-line paragraph
starting with `🤖`), or at the end if there is none. A new description may come with or without
the block (the natural round-trip fetches the body, edits it and passes it back): any block it
carries is dropped, since its content is always rendered from state, and one fresh block placed.

A block is any pair of tags on lines of their own; code blocks are not parsed. The accepted cost: a
description quoting the tags verbatim, each on a line of its own (a PR about ship), has that
example taken for ship's block and replaced. Quote them inline, indented or in a blockquote and
they are the author's text. Tags that don't pair up in order (half a quoted example) are refused,
never guessed at.
"""

import re

_BEGIN = "<!-- ship -->"
_END = "<!-- /ship -->"
# Tags count only on a line of their own: the legacy reference line below ends in the same
# `<!-- ship -->` text. A trailing `\r` is part of the line end: a body edited in GitHub's web UI
# has `\r\n`. The block was `ship:assessment` before it held the reference too; PR bodies on GitHub
# carry that name, so it is read as the block (and renamed on write) ON PURPOSE.
_TAG_RE = re.compile(r"(?m)^<!-- (/?)ship(?::assessment)? -->[ \t]*(?=\r?$)")
_SIGNATURE_PREFIX = "🤖"


# Before the block held it, the reference was a line of its own, marked so it could be told from
# the author's own `Closes #…` lines; before that, the non-closing `Part of #<issue>`. Kept ON
# PURPOSE, not cruft: PR bodies on GitHub carry these, and the first write under the current tags
# drops them for this feature's issue, since the block now carries the reference. Lines naming
# another issue are the author's (an example quoting ship's format) and stay.
def _legacy_ref_re(issue: int) -> re.Pattern:
    return re.compile(rf"(?m)\n*^(?:Closes #{issue} <!-- (?:ship|feature-workflow) -->|Part of #{issue})[ \t]*$")


def _blocks(body: str) -> list[tuple[int, int]]:
    """The `(start, end)` spans of the blocks in `body`, from an opening tag through its closing tag.
    Refuses a body whose tags don't pair up in order: a tag without its pair (half a quoted
    example) leaves no way to tell where ship's block is, and guessing would cut the author's text."""
    tags = list(_TAG_RE.finditer(body))
    if [t.group(1) for t in tags] != ["", "/"] * (len(tags) // 2):
        raise ValueError(
            f"Refusing to rewrite a PR body whose ship tags don't pair up: every {_BEGIN} line needs a "
            f"{_END} line after it, before the next {_BEGIN}. Fix the tags in the PR body by hand."
        )
    return [(o.start(), c.end()) for o, c in zip(tags[::2], tags[1::2])]


def _without(body: str, start: int, end: int) -> str:
    """`body` with `body[start:end]` cut out, and the paragraphs either side joined by one blank line."""
    head, tail = body[:start].rstrip("\r\n"), body[end:].lstrip("\r\n")
    return f"{head}\n\n{tail}" if head and tail else head or tail


def _wrap(content: str) -> str:
    return f"{_BEGIN}\n{content.strip()}\n{_END}"


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
    the invariant of every body ship writes, true by construction, so a failure is a bug in ship."""
    found = [body[start:end] for start, end in _blocks(body)]
    if found != [block]:
        raise ValueError(f"Refusing to write a PR body with {len(found)} ship blocks, not the one just rendered.")
    return body


def with_block(body: str, content: str, issue: int) -> str:
    """`body` with ship's block set to `content`, rewriting only what is between its tags.

    A body that has no block under the current tags yet is one ship wrote before: its old
    reference lines for `issue` are dropped, since the block carries the reference now, and a
    block under the old name is replaced. A body with several blocks (written before ship kept to
    one, or edited by hand) keeps the last one's place and drops the others. After that nothing
    outside the tags changes."""
    blocks = _blocks(body)
    if not blocks or not body.startswith(_BEGIN, blocks[-1][0]):
        body = _legacy_ref_re(issue).sub("", body)
        blocks = _blocks(body)
    block = _wrap(content)
    if not blocks:
        return _one_block(_place(body, block), block)
    *extra, (start, end) = blocks
    body = body[:start] + block + body[end:]
    for s, e in reversed(extra):  # right to left, so the spans still to cut stay where they were
        body = _without(body, s, e)
    return _one_block(body, block)


def with_description(text: str, content: str) -> str:
    """`text` as the new description, with ship's block placed below it. Any block `text` carries
    (it is often the PR body fetched, edited and passed back) is dropped first: nothing in it is
    the author's, since it is rendered from state."""
    for start, end in reversed(_blocks(text)):
        text = _without(text, start, end)
    block = _wrap(content)
    return _one_block(_place(text, block), block)
