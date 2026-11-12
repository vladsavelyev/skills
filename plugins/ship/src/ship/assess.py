"""How deep a PR's review should go — pure sizing logic.

The depth is the reasoning effort the reviewer (Sol) runs at, so a two-line docs fix doesn't pay
for a max-effort sweep and a migration doesn't get a skim. It comes from size, risk and complexity:

- Before any review, nobody has read the change, so nothing is guessed from the diff's content:
  size comes from the diff stats and risk from which paths it touches (`path_risk`). Reading a
  whole diff just to pick an effort cost more than it bought, and did not scale to big PRs.
- After a review, Sol has read every line, so its grade of risk and complexity (recorded with the
  round, and written into the PR body) sets the depth of the next round. A first guess that was
  too low gets corrected by the reviewer that found out.

The mapping lives here, not in the skill, so the same inputs always buy the same depth.
"""

from dataclasses import dataclass
from fnmatch import fnmatch

SIZES = ("S", "M", "L", "XL")
LEVELS = ("low", "med", "high")
# Whether reverting the PR undoes what a bug in it did: a two-way door can be walked back through,
# a one-way door can't (stored data rewritten, data sent out, a cutover others now depend on).
DOORS = ("one-way", "two-way")
# Reviewer reasoning efforts, cheapest first. Sol also offers `ultra`; it's never picked
# automatically, only when the user asks for it.
DEPTHS = ("low", "medium", "high", "xhigh", "max")

# (max files, max changed lines) per size bucket; anything beyond L is XL. M's ceiling matches
# budget.py's "large diff" threshold, so the size label and the extra review round agree.
_SIZE_CEILINGS = (("S", 5, 100), ("M", 15, 500), ("L", 40, 1500))


def size_bucket(changed_files: int, changed_lines: int) -> str:
    for name, max_files, max_lines in _SIZE_CEILINGS:
        if changed_files <= max_files and changed_lines <= max_lines:
            return name
    return "XL"


# Paths whose bugs rarely cost anything: docs and tests. `fnmatch`'s `*` crosses `/`.
LOW_RISK_GLOBS = (
    "*.md",
    "*.rst",
    "*.txt",
    "docs/*",
    "*/docs/*",
    "tests/*",
    "*/tests/*",
    "test_*",
    "*/test_*",
    "*_test.*",
    "*.test.*",
    "*.spec.*",
)


def path_risk(paths: list[str], sensitive_hits: list[str]) -> str:
    """Risk from what the PR touches, before anyone has read it: `high` if it touches a path the
    repo declared sensitive, `low` if it only touches docs and tests, else `med`."""
    if sensitive_hits:
        return "high"
    if paths and all(any(fnmatch(p, g) for g in LOW_RISK_GLOBS) for p in paths):
        return "low"
    return "med"


def last_grade(history: list[dict]) -> dict | None:
    """The reviewer's grade from the newest round that recorded one."""
    return next((e["grade"] for e in reversed(history) if "grade" in e), None)


def review_depth(*, size: str, risk: str, complexity: str) -> str:
    """Risk sets the base depth: low risk reviews at low, med at medium, high at high. A missed
    bug in a low-risk change costs little however big or hard the change is, so size and
    complexity never lift it. Above that, a big or complex diff buys one more step, and both
    together buy two only when the risk is high."""
    if size not in SIZES or risk not in LEVELS or complexity not in LEVELS:
        raise ValueError(f"unknown assessment: size={size!r} risk={risk!r} complexity={complexity!r}")
    if risk == "low":
        return DEPTHS[0]
    bumps = int(size in ("L", "XL")) + int(complexity == "high")
    return DEPTHS[LEVELS.index(risk) + (bumps if risk == "high" else min(bumps, 1))]


# The severities a round's finds are listed under, worst first. `?` is an unlabelled problem.
FOUND_SEVS = ("high", "med", "low", "?")
# How a grade level reads in the PR body.
_SPELLED = {"low": "low", "med": "medium", "high": "high"}


def _found_parts(entry: dict) -> list[str]:
    if "found" in entry:
        parts = [
            f"{len(nums)} {sev} ({', '.join(f'#{n}' for n in nums)})"
            # Known severities worst first, then any other `sev:*` label a problem was given by hand.
            for sev in [*FOUND_SEVS, *sorted(entry["found"].keys() - set(FOUND_SEVS))]
            if (nums := entry["found"].get(sev))
        ]
    else:
        # Rounds recorded before `--found` existed carry only their counts. Kept ON PURPOSE: those
        # rounds are already written into feature issues of PRs in flight.
        counts = (("blocking", entry["new_blocking"]), ("low", entry["new_low"]))
        parts = [f"{n} {kind}" for kind, n in counts if n]
    return parts


def _found(entry: dict) -> str:
    parts = _found_parts(entry)
    if not parts:
        return "none"
    regressions = f", {entry['regressions']} of them back after a fix" if entry["regressions"] else ""
    return ", ".join(parts) + regressions


def _round_line(entry: dict) -> str:
    title = f"**Review round {entry['run']}**"
    if entry.get("placeholder"):
        return f"{title}. Not recorded."
    # Rounds from before review sizing carry no depth; also already written into feature issues.
    depth = f" ({entry['depth']})" if "depth" in entry else ""
    return f"{title}{depth}. Findings: {_found(entry)}."


@dataclass(frozen=True)
class Review:
    """What the PR body shows once a review has graded the PR."""

    grade: dict
    size: str
    changed_files: int
    changed_lines: int
    history: list[dict]
    next_depth: str


def _next_round(last: dict, depth: str) -> str:
    """The depth the next round would run at. After a clean round no round is due, so the line
    says one only follows new commits (they move the head off the reviewed SHA and reopen the gate),
    rather than read as if another round were pending."""
    if not last.get("placeholder") and not _found_parts(last):
        return f" A re-review after new commits would run at {depth}."
    return f" Next round would run at {depth}."


def _risk_lines(grade: dict) -> list[str]:
    """The grade's risk, read as its two halves: whether a revert undoes a bug (the door) and what
    a bug would reach (the blast radius)."""
    risk, why = _SPELLED[grade["risk"]], grade["why"].strip()
    if "door" not in grade:
        # Grades recorded before the door and blast radius existed carry neither. Kept ON PURPOSE:
        # they are already written into feature issues of PRs in flight.
        return [f"**Risk: {risk}**: {why}"]
    return [f"**Risk: {risk}**, {grade['door']} door: {why}", f"Blast radius: {grade['blast_radius'].strip()}"]


def render(*, issue: int, review: Review | None) -> str:
    """The quote in the PR body's ship block: Sol's latest grade, the closing reference to the
    feature issue, and one line per review round. A round's line lists what it found, which never
    changes afterwards. Before any review it is the reference alone."""
    # `closes` is what makes GitHub close the issue on merge; a bare `#39` would only link it.
    lines = [f"Feature tracking issue: closes #{issue}"]
    if review is not None:
        grade = review.grade
        runs = [e for e in review.history if "run" in e]
        rounds = [_round_line(e) for e in runs]
        rounds[-1] += _next_round(runs[-1], review.next_depth)
        lines = [
            *_risk_lines(grade),
            f"Complexity: {_SPELLED[grade['complexity']]}, size: {review.size} "
            f"({review.changed_files} files, {review.changed_lines} lines)",
            *lines,
            *rounds,
        ]
    return "\n".join(f"> {line}" for line in lines)
