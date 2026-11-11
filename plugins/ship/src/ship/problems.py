"""Which problem sub-issues hold the merge gate — pure classification over sub-issue dicts.

**Every open problem blocks the merge.** Nothing ships as debt. A problem a review filed is
either fixed (closed by `feature problem resolve` with its commit) or not relevant (closed by
`feature problem wontfix`, which applies the `wontfix` label and posts the reason). Severity is
still recorded on each problem, for the round counts and the convergence trend, but it no longer
decides whether a problem holds the gate: an earlier design let `sev:low` problems and `deferred`
ones ship open, and they piled up as "tracked debt" nobody ever came back to.

A closed problem that turns out to matter after all (a later round rediscovering it with a real
repro) is reopened by `feature problem block`, and being open is all it takes to block again.

**After the merge, an open problem can be handed off.** A PR merged past the gate can leave
problems open, and its fix can only come in a new PR. Holding the feature's close-out until that
PR lands bought nothing, since the problem is already a GitHub issue. So once the PR has merged,
the user chooses, per problem: a separate fix PR, or keep it as an issue. Either way `feature
problem hand-off` labels it `handed-off` and it stays open on its own, outside the feature. Before
the merge there is no such option: that would be shipping debt. (A PR closed unmerged never landed
its problems, so those close as `wontfix`.)
"""

UNKNOWN_SEV = "sev:?"
LOW_SEV = "sev:low"
WONTFIX = "wontfix"
HANDED_OFF = "handed-off"
# Labels the old CLI already wrote onto problem issues on GitHub, kept ON PURPOSE: `rejected` was its
# not-relevant close (now `wontfix`), and `follow-up` its close-out decision to keep a merged PR's
# problem open as a backlog issue (now `handed-off`). Without these, `problem list` would call old
# rejections fixed, and reconcile would hold archived features on decisions already made.
LEGACY_WONTFIX = "rejected"
LEGACY_HANDED_OFF = "follow-up"


def severity(sub: dict) -> str:
    """The `sev:*` label on a problem, or `sev:?` if it has none."""
    return next((label for label in sub["labels"] if label.startswith("sev:")), UNKNOWN_SEV)


def is_blocking(sub: dict) -> bool:
    """True if this sub-issue holds the merge gate: every open problem does, at every severity."""
    return sub["state"] == "OPEN"


def blocking(subs: list[dict]) -> list[dict]:
    """The problems that hold the gate: all the open ones."""
    return [s for s in subs if is_blocking(s)]


def is_wontfix(sub: dict) -> bool:
    """Closed as not relevant, by this CLI or (as `rejected`) the old one."""
    return WONTFIX in sub["labels"] or LEGACY_WONTFIX in sub["labels"]


def is_handed_off(sub: dict) -> bool:
    """Handed off after the merge, by this CLI or (as `follow-up`) the old one."""
    return HANDED_OFF in sub["labels"] or LEGACY_HANDED_OFF in sub["labels"]


def undecided(subs: list[dict]) -> list[dict]:
    """The problems that hold the close-out: open, and not handed off after the merge."""
    return [s for s in blocking(subs) if not is_handed_off(s)]


def handed_off(subs: list[dict]) -> list[dict]:
    """Open problems of a merged PR, handed to a separate fix PR or kept as an issue."""
    return [s for s in blocking(subs) if is_handed_off(s)]
