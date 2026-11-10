"""Ship's block in a PR body: one tagged region the CLI owns, and the only part of the body it edits.

    <the author's description>

    <!-- ship -->
    > **Risk: low**: …
    > …

    Closes #39
    <!-- /ship -->

    🤖 <model> in <harness>

`feature review record` sets the quoted assessment and `feature pr` the `Closes` line, each
rewriting only what is between the tags. A body without the block gets it above the author's
signature (a trailing paragraph starting with `🤖`), or at the end if there is none.
"""

import re

_BEGIN = "<!-- ship -->"
_END = "<!-- /ship -->"
# Tags count only on a line of their own: the legacy reference line below ends in the same
# `<!-- ship -->` text. The block was `ship:assessment` before it held the reference too; PR bodies
# on GitHub carry that name, so it is read as the block (and renamed on write) ON PURPOSE.
_BLOCK_RE = re.compile(r"(?ms)^<!-- ship(?::assessment)? -->[ \t]*$(.*?)^<!-- /ship(?::assessment)? -->[ \t]*$")
_REF_RE = re.compile(r"Closes #\d+")
_SIGNATURE_PREFIX = "🤖"
# Before the block held it, the reference was a line of its own, marked so it could be told from
# the author's own `Closes #…` lines; before that, the non-closing `Part of #<issue>`. Kept ON
# PURPOSE, not cruft: PR bodies on GitHub carry these, and each write moves such a line into the
# block instead of leaving a second reference behind.
_LEGACY_REF_RE = re.compile(r"(?m)\n*^Closes #(\d+) <!-- (?:ship|feature-workflow) -->[ \t]*$")


def _legacy_part_of_re(issue: int) -> re.Pattern:
    return re.compile(rf"(?m)\n*^Part of #{issue}[ \t]*$")


def _block_parts(body: str) -> tuple[str, str]:
    """The assessment and the `Closes` line in `body`'s block, each empty if absent."""
    block = _BLOCK_RE.search(body)
    if not block:
        return "", ""
    inner = block.group(1).strip()
    head, _, last = inner.rpartition("\n\n")
    return (head, last) if _REF_RE.fullmatch(last) else (inner, "")


def _without_legacy_ref(body: str, issue: int | None = None) -> tuple[str, str]:
    """`body` without a reference line from before the block held it, and that reference. Only
    while the block holds no reference: once it does, nothing outside the block is touched."""
    if _block_parts(body)[1]:
        return body, ""
    found = [f"Closes #{m.group(1)}" for m in _LEGACY_REF_RE.finditer(body)]
    body = _LEGACY_REF_RE.sub("", body)
    if issue is not None:
        body = _legacy_part_of_re(issue).sub("", body)
    return body, found[-1] if found else ""


def _with_block(body: str, assessment: str, ref: str) -> str:
    """`body` with its block set to `assessment` and `ref`: replaced in place if it has one,
    otherwise placed above the trailing signature or at the end."""
    inner = "\n\n".join(p for p in (assessment.strip(), ref) if p)
    block = f"{_BEGIN}\n{inner}\n{_END}"
    if _BLOCK_RE.search(body):
        return _BLOCK_RE.sub(lambda _: block, body, count=1)
    if not inner:
        return body
    text = body.rstrip()
    head, _, last = text.rpartition("\n\n")
    if last.startswith(_SIGNATURE_PREFIX):
        return f"{head}\n\n{block}\n\n{last}\n" if head else f"{block}\n\n{last}\n"
    return f"{text}\n\n{block}\n" if text else f"{block}\n"


def with_ref(body: str, issue: int) -> str:
    """`body` linked to feature issue `issue`: the block's `Closes` line set to it."""
    body, _ = _without_legacy_ref(body, issue)
    assessment, _ = _block_parts(body)
    return _with_block(body, assessment, f"Closes #{issue}")


def with_assessment(body: str, text: str) -> str:
    """`body` with the block's assessment set to `text`."""
    body, legacy = _without_legacy_ref(body)
    _, ref = _block_parts(body)
    return _with_block(body, text, ref or legacy)


def with_description(body: str, text: str) -> str:
    """`text` as the new description, carrying `body`'s block over. `text` is taken as written."""
    body, legacy = _without_legacy_ref(body)
    assessment, ref = _block_parts(body)
    return _with_block(text, assessment, ref or legacy)
