"""The PR body's layout: the author's prose, then ship's meta, then the author's signature.

    <prose>

    <!-- ship:assessment -->
    > **Risk: high**: …
    > …
    <!-- /ship:assessment -->

    Closes #4473 <!-- ship -->

    🤖 <model> in <harness>

The prose (with any signature the author wrote) comes from `feature start --body` or `feature
describe`; the meta is the CLI's own and is written by `feature review record` and `feature pr`.
Every writer parses the whole body into `Parts` and composes it back, so the layout comes out the
same whichever of them ran last.

The meta is read only from the END of the body, where every writer has always put it. Matching
ship's lines anywhere would take a quoted example (in a code block, in a PR about ship itself) or
an author's own `Part of #12` for ours. Bodies written before this layout carry the meta after the
signature instead of before it; reading the tail in any order puts those right on the next write.
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
_FEATURE_REF_RE = re.compile(r"(?i)(?:Closes #\d+ <!-- (?:ship|feature-workflow) -->|Part of #\d+)")

_ASSESS_BEGIN = "<!-- ship:assessment -->"
_ASSESS_END = "<!-- /ship:assessment -->"

# A signature: a one-line paragraph starting with this, the attribution line an agent harness
# asks for (`🤖 Claude Opus 5.5 in Claude Code (T3 Code)`). One line, so the end of a code block
# that happens to quote one (`🤖 …` then the closing fence) is never taken for it.
_SIGNATURE_PREFIX = "🤖"
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n")


@dataclass(frozen=True)
class Parts:
    prose: str
    signature: str
    assessment: str  # the text between the markers, without them
    ref: str  # our `Closes #<issue>` line


def feature_ref(issue: int) -> str:
    """The reference line this workflow writes into a PR body to link it to its feature issue."""
    return f"Closes #{issue} {_REF_MARK}"


def _split_last_paragraph(text: str) -> tuple[str, str]:
    breaks = list(_BLANK_LINE_RE.finditer(text))
    if not breaks:
        return "", text
    return text[: breaks[-1].start()].rstrip(), text[breaks[-1].end() :].strip()


def _is_signature(paragraph: str) -> bool:
    return paragraph.startswith(_SIGNATURE_PREFIX) and "\n" not in paragraph


def split_signature(text: str) -> tuple[str, str]:
    """`text` split into its prose and its trailing signature paragraphs."""
    rest, signature = text.strip(), []
    while rest:
        head, last = _split_last_paragraph(rest)
        if not _is_signature(last):
            break
        signature.insert(0, last)
        rest = head
    return rest, "\n\n".join(signature)


def parse(body: str) -> Parts:
    """Peel the meta and the signature off the end of `body`, in whatever order they come; the
    first paragraph that is none of them, and everything above it, is the prose."""
    rest, signature, assessment, ref = body.strip(), [], "", ""
    while rest:
        if not assessment and rest.endswith(_ASSESS_END) and (begin := rest.rfind(_ASSESS_BEGIN)) != -1:
            assessment = rest[begin + len(_ASSESS_BEGIN) : -len(_ASSESS_END)].strip()
            rest = rest[:begin].rstrip()
            continue
        head, last = _split_last_paragraph(rest)
        if _is_signature(last):
            signature.insert(0, last)
        elif not ref and _FEATURE_REF_RE.fullmatch(last):
            ref = last
        else:
            break
        rest = head
    return Parts(prose=rest, signature="\n\n".join(signature), assessment=assessment, ref=ref)


def compose(parts: Parts) -> str:
    assessment = f"{_ASSESS_BEGIN}\n{parts.assessment}\n{_ASSESS_END}" if parts.assessment else ""
    blocks = [b for b in (parts.prose, assessment, parts.ref, parts.signature) if b]
    return "\n\n".join(blocks) + "\n" if blocks else ""


def _write(parts: Parts) -> str:
    """`parts` composed into a body, refused unless the body reads back as exactly those parts.

    Every writer changes one part and carries the others over untouched, so this is what makes a
    write unable to lose or alter anyone's text: the only way to fail it is a description whose
    own last paragraph looks like ship's (a `🤖` line, a `Part of #N`, a ship marker), which the
    next read would take for meta. Refusing that write beats letting a later one rewrite it."""
    body = compose(parts)
    if (back := parse(body)) != parts:
        changed = ", ".join(
            f for f in ("prose", "signature", "assessment", "ref") if getattr(back, f) != getattr(parts, f)
        )
        raise ValueError(
            f"Refusing to write the PR body: it would read back with a different {changed}. The "
            f"description's last paragraph looks like one of ship's own lines (a signature, a "
            f"`Part of #N`, a ship marker); reword it."
        )
    return body


def with_ref(body: str, issue: int) -> str:
    """`body` linked to feature issue `issue`, replacing the reference line of ours it had."""
    return _write(replace(parse(body), ref=feature_ref(issue)))


def with_assessment(body: str, text: str) -> str:
    """`body` with its assessment section set to `text`."""
    return _write(replace(parse(body), assessment=text.strip()))


def with_description(body: str, text: str) -> str:
    """`body` with its prose and signature replaced by `text`'s, keeping the meta this workflow
    owns. `text` is the author's, so only its trailing signature is split off: a ship line it
    quotes is prose."""
    prose, signature = split_signature(text)
    return _write(replace(parse(body), prose=prose, signature=signature))
