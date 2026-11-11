"""Which problem sub-issues hold the merge gate — pure classification over sub-issue dicts.

**Every open problem blocks the merge.** Nothing ships as debt. A problem a review filed is
either fixed (closed by `feature problem resolve` with its commit) or not relevant (closed by
`feature problem wontfix`, which applies the `wontfix` label and posts the reason). Severity is
still recorded on each problem, for the round counts and the convergence trend, but it no longer
decides whether a problem holds the gate: an earlier design let `sev:low` problems and `deferred`
ones ship open, and they piled up as "tracked debt" nobody ever came back to.

A closed problem that turns out to matter after all (a later round rediscovering it with a real
repro) is reopened by `feature problem block`, and being open is all it takes to block again.
"""

UNKNOWN_SEV = "sev:?"
LOW_SEV = "sev:low"
WONTFIX = "wontfix"


def severity(sub: dict) -> str:
    """The `sev:*` label on a problem, or `sev:?` if it has none."""
    return next((label for label in sub["labels"] if label.startswith("sev:")), UNKNOWN_SEV)


def is_blocking(sub: dict) -> bool:
    """True if this sub-issue holds the merge gate: every open problem does, at every severity."""
    return sub["state"] == "OPEN"


def blocking(subs: list[dict]) -> list[dict]:
    """The problems that hold the gate (and the close-out): all the open ones."""
    return [s for s in subs if is_blocking(s)]
