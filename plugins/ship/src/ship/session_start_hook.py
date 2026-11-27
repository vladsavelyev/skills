#!/usr/bin/env python3
"""SessionStart hook: inject feature context for the current branch into the session.

The plugin wires it in hooks/hooks.json. The hook prints a short context block to stdout (which Claude Code injects into context).
If the branch isn't a tracked feature, it prints nothing and exits 0 — a non-feature branch
is a normal case, not an error.
"""

import io
import subprocess
from contextlib import redirect_stdout

from . import github
from .__main__ import cmd_status
from .gitwiring import current_branch, feature_issue
from .state import StaleSchema


class _Args:
    branch = None
    json = False


def main() -> None:
    # The only "expected absent" case is running outside a git repo: `git rev-parse` exits
    # non-zero. That's not an error for a hook that may fire anywhere, so we return quietly.
    # ANY OTHER failure (broken wiring, gh unauthenticated, malformed state) propagates and
    # aborts loudly — per repo policy we never hide problems behind a blanket catch.
    try:
        branch = current_branch()
    except subprocess.CalledProcessError:
        return  # not inside a git repo; nothing to inject

    if feature_issue(branch) is None:
        return  # not a tracked feature; nothing to inject
    github.use_owner_account()

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            cmd_status(_Args())
    except StaleSchema as exc:
        # A state block this CLI doesn't understand is the one failure with a known, one-command fix,
        # and it fires on EVERY session start until someone applies it. Surfacing the message (which
        # names `feature migrate`) beats a traceback in the session-context hook. Caught by its own
        # exception type, not by `ValueError`: `cmd_status` also makes several `gh` calls whose
        # parsing raises plain ValueErrors, and reporting those as "run feature migrate" would be
        # advice that cannot fix them. Nothing else is softened.
        print("<feature-context>")
        print(f"This branch is a TRACKED FEATURE, but its state could not be read: {exc}")
        print("Run `feature migrate` on this branch before using any other `feature` command.")
        print("</feature-context>")
        return

    print("<feature-context>")
    print(buf.getvalue().rstrip())
    print()
    # Steer review requests into the tracked flow. Without this, a bare "run a review" on a
    # tracked branch gets served by whatever generic reviewer the model reaches for first — it
    # never loads the `ship` skill, so the finding never becomes a tracked problem and
    # the merge gate never moves. Naming the skill explicitly (not just its trigger phrases)
    # makes the routing deterministic regardless of skill auto-matching.
    print(
        "This branch is a TRACKED FEATURE. A request to ship it, babysit it, or fix what its "
        "review found, MUST go through `Skill(ship:ship)`: one run reviews in a fork, fixes every "
        "problem it files in another, reviews again until the gate settles, and babysits the PR "
        "through CI, its reviewers and its base until it's ready to merge. A request only to "
        "review it, without fixing, is `Skill(ship:review)`, NOT a standalone code reviewer. "
        "Checking its merge gate or its status is `feature gate` or `feature status`."
    )
    # A merged PR whose feature never closed out is the one state where reviewing is pointless, and
    # the branch you're on is the last place anyone will notice — after this session it gets deleted
    # and the tracking issue is orphaned for good. The gate says MERGED there; route it to the
    # close-out skill, which never reviews, rather than to `ship`, which exists to review.
    print(
        "If the gate says MERGED, the PR is in: don't review it. Close the feature out with "
        "`Skill(ship:close)`, which records what shipped and closes the tracking issue."
    )
    # Otherwise the gate line says what to do; make the stop condition explicit, because the failure
    # mode this replaced was an agent reviewing round after round chasing a clean sweep.
    print(
        "Before the merge, the gate decides whether to review again: REVIEW_AGAIN means close "
        "the open problems and run ONE more review round; REDESIGN means a kind of bug came back "
        "after its fix: redesign in a fix round without asking, closing the problems the redesign "
        "fixes, `feature redesign --reason '<cause>'`, then review it; NEEDS_DECISION means STOP "
        "reviewing, still close the open problems (the budget limits reviews, not fixes), and "
        "`feature escalate --reason '<recommendation>'` for a human. Nothing ships as debt: "
        "every open problem blocks, at every severity, until it is fixed (`feature problem "
        "resolve <#> --commit <sha>`) or closed as not relevant (`feature problem wontfix <#> "
        "--reason '<why>'`)."
    )
    print("</feature-context>")


if __name__ == "__main__":
    main()
