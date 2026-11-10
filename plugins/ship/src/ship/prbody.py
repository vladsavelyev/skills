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
the tags. A body without the block gets it above the author's signature (a trailing one-line
paragraph starting with `🤖`), or at the end if there is none.
"""

import re

_BEGIN = "<!-- ship -->"
_END = "<!-- /ship -->"
# Tags count only on a line of their own: the legacy reference line below ends in the same
# `<!-- ship -->` text. The block was `ship:assessment` before it held the reference too; PR bodies
# on GitHub carry that name, so it is read as the block (and renamed on write) ON PURPOSE.
_BLOCK_RE = re.compile(r"(?ms)^<!-- ship(?::assessment)? -->[ \t]*$.*?^<!-- /ship(?::assessment)? -->[ \t]*$")
_SIGNATURE_PREFIX = "🤖"


# Before the block held it, the reference was a line of its own, marked so it could be told from
# the author's own `Closes #…` lines; before that, the non-closing `Part of #<issue>`. Kept ON
# PURPOSE, not cruft: PR bodies on GitHub carry these, and the first write under the current tags
# drops them for this feature's issue, since the block now carries the reference. Lines naming
# another issue are the author's (an example quoting ship's format) and stay.
def _legacy_ref_re(issue: int) -> re.Pattern:
    return re.compile(rf"(?m)\n*^(?:Closes #{issue} <!-- (?:ship|feature-workflow) -->|Part of #{issue})[ \t]*$")


def _block(body: str) -> re.Match | None:
    """Ship's block in `body`: the last one, since it sits below the description, and a
    description that quotes ship's tags (a PR about ship) has its example above it."""
    return next(reversed(list(_BLOCK_RE.finditer(body))), None)


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


def with_block(body: str, quote: str, issue: int) -> str:
    """`body` with ship's block set to `quote`, rewriting only what is between its tags.

    A body that has no block under the current tags yet is one ship wrote before: its old
    reference lines for `issue` are dropped, since the block carries the reference now, and a
    block under the old name is replaced. After that first write nothing outside the tags changes."""
    block = _wrap(quote)
    existing = _block(body)
    if existing is None or not existing.group(0).startswith(_BEGIN):
        body = _legacy_ref_re(issue).sub("", body)
        existing = _block(body)
    if existing is not None:
        return body[: existing.start()] + block + body[existing.end() :]
    return _place(body, block)


def with_description(text: str, quote: str) -> str:
    """`text` as the new description, taken as written, with ship's block placed below it. `text`
    is never searched for a block: a description can quote ship's tags."""
    return _place(text, _wrap(quote))
