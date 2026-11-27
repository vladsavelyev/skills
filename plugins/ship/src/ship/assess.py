"""The reviewer's grade of a PR, and the ship block that shows it in the PR body — pure logic.

Every round runs at the same reasoning effort (`reviewer.EFFORT`), whatever the PR's size or risk.
The reviewer, which reads every line, grades the PR's risk and complexity as part of its review;
the grade is recorded with the round and shown to the human beside the PR's size from its diff
stats. Before anyone has read a change, `path_risk` judges it from the paths it touches, for the
gate's question of whether a commit needs a review at all.
"""

from dataclasses import dataclass
from fnmatch import fnmatch

from .state import found_in

LEVELS = ("low", "med", "high")
# Whether reverting the PR undoes what a bug in it did: a two-way door can be walked back through,
# a one-way door can't (stored data rewritten, data sent out, a cutover others now depend on).
DOORS = ("one-way", "two-way")

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
    return f"{title}. Findings: {_found(entry)}."


@dataclass(frozen=True)
class Review:
    """What the PR body shows once a review has graded the PR."""

    grade: dict
    size: str
    changed_files: int
    changed_lines: int
    history: list[dict]
    open_problems: frozenset[int]


def _grade_lines(review: Review) -> list[str]:
    """The reviewer's grade of the risk, read as its two halves (the door: whether a revert undoes what a bug
    did; the blast radius: who or what a bug reaches), then of the complexity, with the PR's size."""
    grade = review.grade
    risk, why = _SPELLED[grade["risk"]], grade["why"].strip()
    complexity = (
        f"**Complexity:** {_SPELLED[grade['complexity']]}, size {review.size} "
        f"({review.changed_files} files, {review.changed_lines} lines)"
    )
    if "door" not in grade:
        # Grades recorded before the door and blast radius existed carry neither. Kept ON PURPOSE:
        # they are already written into feature issues of PRs in flight.
        return [f"**Risk: {risk}**: {why}", complexity]
    door = f"**Risk: {risk}**, {grade['door']} door: {why}"
    return [door, f"**Blast radius:** {grade['blast_radius'].strip()}", complexity]


def render(*, issue: int, review: Review | None) -> str:
    """The content of the PR body's ship block: one quote, with no header, so it reads as a footnote
    to the author's description. Once a review has graded the PR, it opens with the reviewer's latest grade;
    then the closing reference to the feature issue, and one line per review round whose problems are
    all closed. A round's line lists what it found, which never changes afterwards, and it waits
    until every one of those problems is fixed or `wontfix`: the block reports a round once its
    findings are dealt with, never a list of problems still waiting for their fix. GitHub breaks a PR
    body's lines where they break here."""
    # `closes` is what makes GitHub close the issue on merge; a bare `#39` would only link it.
    reference = f"**Tracking issue:** closes #{issue}"
    if review is None:
        return f"> {reference}"
    runs = [e for e in review.history if "run" in e]
    shown = [e for e in runs if not found_in([e]) & review.open_problems]
    rounds = [_round_line(e) for e in shown]
    return "\n".join(f"> {line}" for line in [*_grade_lines(review), reference, *rounds])
