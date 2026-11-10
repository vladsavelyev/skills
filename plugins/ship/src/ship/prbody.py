"""The PR body's layout: the author's prose, then ship's meta, then the author's signature.

    <prose>

    ---

    <!-- ship:assessment -->
    **Risk high · complexity low** · size S (2 files, 9 lines)
    …
    <!-- /ship:assessment -->

    Closes #4473 <!-- ship -->

    🤖 <model> in <harness>

The prose (with any signature the author wrote) comes from `feature start --body` or `feature
describe`; the meta is the CLI's own and is written by `feature review record` and `feature pr`.
Every writer parses the whole body into `Parts` and composes it back, so the layout comes out the
same whichever of them ran last, and a body written under an older layout (meta appended after the
signature) is put right on its next write.
"""

import re
from dataclasses import dataclass, replace

# The marker makes the line unmistakably *ours*. A PR body legitimately carries hand-written
# closing references to the problem sub-issues it fixes ("Closes #2173, #2174"), and one of those
# can be a lone `Closes #2173` on its own line — indistinguishable from the feature reference
# without this, so re-linking would strip it and silently stop that problem from auto-closing.
# Invisible when rendered, and GitHub still parses the keyword reference that precedes it.
_REF_MARK = "<!-- ship -->"
# Our own reference line, in any form: the current one, the same line under the marker PRs carried
# before the project was renamed from feature-workflow, or the non-closing `Part of #…` from before
# the reference became a closing one. Each is upgraded in place on the next write.
# The old forms are kept ON PURPOSE, not cruft: they are already written into PR bodies on GitHub,
# and dropping one would make `feature pr` stack a second reference line instead of replacing it.
_FEATURE_REF_RE = re.compile(r"(?mi)^(?:Closes #\d+ <!-- (?:ship|feature-workflow) -->|Part of #\d+)[ \t]*$")

_ASSESS_BEGIN = "<!-- ship:assessment -->"
_ASSESS_END = "<!-- /ship:assessment -->"
_ASSESS_RE = re.compile(re.escape(_ASSESS_BEGIN) + r".*?" + re.escape(_ASSESS_END), re.S)

# The rule `compose` puts between the prose and the meta. Stripped from the end of the prose on
# parse, so it is never doubled.
_SEPARATOR = "---"
_RULE_RE = re.compile(r"[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*")
# A signature paragraph: the attribution line an agent harness asks for, e.g.
# `🤖 Claude Opus 5.5 in Claude Code (T3 Code)`.
_SIGNATURE_PREFIX = "🤖"
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")
# CommonMark fences. Anything inside one is the author's quoted text, never our meta: a PR about
# ship itself can quote a `Closes #N <!-- ship -->` line or a signature in a code block.
_FENCE_OPEN_RE = re.compile(r"(?m)^ {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class Parts:
    prose: str
    signature: str
    assessment: str  # the text between the markers, without them
    ref: str  # our `Closes #<issue>` line


def feature_ref(issue: int) -> str:
    """The reference line this workflow writes into a PR body to link it to its feature issue."""
    return f"Closes #{issue} {_REF_MARK}"


def _fenced_spans(text: str) -> list[tuple[int, int, bool]]:
    """`(start, end, closed)` for every fenced code block in `text`, fence lines included. An
    unclosed fence runs to the end, as it renders."""
    spans, pos = [], 0
    while opener := _FENCE_OPEN_RE.search(text, pos):
        fence = opener.group(1)
        closer = re.compile(rf"(?m)^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$")
        line_end = text.find("\n", opener.end())
        close = closer.search(text, line_end) if line_end != -1 else None
        end = close.end() if close else len(text)
        spans.append((opener.start(), end, close is not None))
        pos = end
    return spans


def _outside(spans: list[tuple[int, int, bool]], start: int, end: int) -> bool:
    return all(end <= s or start >= e for s, e, _ in spans)


def _unfenced(pattern: re.Pattern, text: str) -> list[re.Match]:
    spans = _fenced_spans(text)
    return [m for m in pattern.finditer(text) if _outside(spans, m.start(), m.end())]


def _without(text: str, matches: list[re.Match]) -> str:
    for m in reversed(matches):
        text = text[: m.start()] + text[m.end() :]
    return text


def _split_last_paragraph(text: str) -> tuple[str, str]:
    breaks = list(_BLANK_LINE_RE.finditer(text))
    if not breaks:
        return "", text
    return text[: breaks[-1].start()].rstrip(), text[breaks[-1].end() :]


def _peel_signature(text: str) -> tuple[str, str]:
    """Split the author's text into its prose and its trailing signature paragraphs, dropping a
    rule left dangling at the end of the prose (ours, from the last compose)."""
    rest, signature = text.strip(), []
    spans = _fenced_spans(rest)  # peeling only shortens `rest` from the end, so these stay valid
    while rest:
        head, last = _split_last_paragraph(rest)
        if not _outside(spans, len(rest) - len(last), len(rest)):
            break
        if last.lstrip().startswith(_SIGNATURE_PREFIX):
            signature.insert(0, last.strip())
        elif not _RULE_RE.fullmatch(last):
            break
        rest = head
    return rest, "\n\n".join(signature)


def parse(body: str) -> Parts:
    assessments = _unfenced(_ASSESS_RE, body)
    assessment = assessments[0] if assessments else None
    rest = _without(body, assessments)
    refs = _unfenced(_FEATURE_REF_RE, rest)
    prose, signature = _peel_signature(_without(rest, refs))
    return Parts(
        prose=prose,
        signature=signature,
        assessment=assessment.group(0)[len(_ASSESS_BEGIN) : -len(_ASSESS_END)].strip() if assessment else "",
        ref=refs[-1].group(0).strip() if refs else "",
    )


def compose(parts: Parts) -> str:
    # Whatever follows an unclosed fence renders inside it: the meta would show as code and GitHub
    # would not act on the `Closes` line. Closing the fence for the author would rewrite their text.
    if any(not closed for *_, closed in _fenced_spans(parts.prose)):
        raise ValueError(
            "The PR description has a code fence (``` or ~~~) that is never closed, so the meta "
            "written after it would render as code. Close the fence in the description first."
        )
    meta = [
        s for s in (f"{_ASSESS_BEGIN}\n{parts.assessment}\n{_ASSESS_END}" if parts.assessment else "", parts.ref) if s
    ]
    blocks = [parts.prose] if parts.prose else []
    if meta:
        if blocks:
            blocks.append(_SEPARATOR)
        blocks += meta
    if parts.signature:
        blocks.append(parts.signature)
    return "\n\n".join(blocks) + "\n" if blocks else ""


def with_ref(body: str, issue: int) -> str:
    """`body` linked to feature issue `issue`, replacing any reference line of ours it had."""
    return compose(replace(parse(body), ref=feature_ref(issue)))


def with_assessment(body: str, text: str) -> str:
    """`body` with its assessment section set to `text`."""
    return compose(replace(parse(body), assessment=text.strip()))


def with_description(body: str, text: str) -> str:
    """`body` with its prose and signature replaced by `text`'s, keeping the meta this workflow
    owns."""
    new = parse(text)
    return compose(replace(parse(body), prose=new.prose, signature=new.signature))
