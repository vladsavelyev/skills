"""How deep a PR's review should go — pure sizing logic.

`feature assess` writes a short assessment into the PR body: size (measured from the diff), risk
and complexity (judged by the agent, which has read the change), and the review depth they imply.
The depth is the reasoning effort the reviewer (Sol) runs at, so a two-line docs fix doesn't pay
for a max-effort sweep and a migration doesn't get a skim. The mapping lives here, not in the
skill, so the same inputs always buy the same depth.
"""

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


def render(
    *, size: str, changed_files: int, changed_lines: int, risk: str, complexity: str, depth: str, why: str
) -> str:
    return (
        f"**Review depth: {depth}** · size {size} ({changed_files} files, {changed_lines} lines) · "
        f"risk {risk} · complexity {complexity}\n{why.strip()}"
    )
