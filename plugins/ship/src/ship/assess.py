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

from fnmatch import fnmatch

SIZES = ("S", "M", "L", "XL")
LEVELS = ("low", "med", "high")
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
    """Risk sets the base depth; a big or complex diff buys one more step, and both together
    buy two only when the risk is high. A small, simple, low-risk change gets the lightest pass."""
    if size not in SIZES or risk not in LEVELS or complexity not in LEVELS:
        raise ValueError(f"unknown assessment: size={size!r} risk={risk!r} complexity={complexity!r}")
    if risk == "low" and complexity == "low" and size == "S":
        return DEPTHS[0]
    level = {"low": 1, "med": 2, "high": 3}[risk]
    bumps = int(size in ("L", "XL")) + int(complexity == "high")
    level += bumps if risk == "high" else min(bumps, 1)
    return DEPTHS[min(level, len(DEPTHS) - 1)]


def render(*, size: str, changed_files: int, changed_lines: int, grade: dict, reviewed_at: str, next_depth: str) -> str:
    return (
        f"**Risk {grade['risk']} · complexity {grade['complexity']}** · size {size} "
        f"({changed_files} files, {changed_lines} lines)\n{grade['why'].strip()}\n\n"
        f"Reviewed by Sol at {reviewed_at}; a next round would run at {next_depth}."
    )
