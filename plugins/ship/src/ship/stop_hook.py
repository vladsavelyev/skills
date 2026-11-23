"""Stop hook: a session holding a feature (`hold.py`) can't end its turn until the gate settles.

Claude Code runs it whenever the agent is about to end its turn. For each branch this session holds,
it judges the gate. OPEN or MERGED: the run is over, and the hold goes. Anything else: it blocks the
stop, and Claude Code hands the reason back to the agent, which goes on with what the gate asks
for: fix the open problems, review again, or leave on the record (`feature escalate`, `feature
release`).
"""

import json
import os
import sys
from pathlib import Path

from . import feature, github, hold, problems
from .gitwiring import worktree_for_branch


def _why(branch: str, worktree: str | None, issue: int, state: dict, blocking: list[dict], decision) -> str:
    listed = "\n".join(f"  #{s['number']} [{problems.severity(s)}] {s['title']}" for s in blocking) or "  none"
    return (
        f"ship: this session is shipping `{branch}` (PR #{state['pr']}, feature #{issue}), and its gate is "
        f"{decision}. The ship run isn't over, so don't end the turn here.\n"
        f"Open problems, every one blocking:\n{listed}\n"
        f"Next: {decision.next_step}\n"
        f"Fixing is part of the run: fix each problem at its root cause in {worktree or 'the feature worktree'}, "
        f"commit and push, `feature problem resolve <#> --commit <sha>` (or `feature problem wontfix <#> "
        f"--reason '<why>'` if it isn't relevant), and while the gate says REVIEW_AGAIN, review again with "
        f"`Skill(ship:ship)`.\n"
        f"Only two ways out short of OPEN, both on the record: `feature escalate --reason '<…>' [--problem <#> …]` "
        f"when the user must decide (the budget is spent with every problem closed, or a problem needs their "
        f"call), and `feature release --reason '<…>'` when the user stopped the run or the review can't run."
    )


def main() -> None:
    payload = json.load(sys.stdin)
    session = payload["session_id"]
    holds = hold.held(session)
    blocks: list[str] = []
    gone: list[str] = []
    own_token = "GH_TOKEN" in os.environ
    for held in holds:
        branch, checkout = held["branch"], held["checkout"]
        if not Path(checkout).is_dir():
            hold.release(session, branch)
            gone.append(f"ship: stopped holding `{branch}`: its checkout {checkout} is gone.")
            continue
        os.chdir(checkout)
        if not own_token:
            # Holds may be in repos of different accounts; each picks its own (`use_owner_account`).
            os.environ.pop("GH_TOKEN", None)
        github.use_owner_account()
        issue, state, blocking, decision = feature.gate_for(branch)
        if decision.settled:
            hold.release(session, branch)
            continue
        blocks.append(_why(branch, worktree_for_branch(branch), issue, state, blocking, decision))
    out: dict = {}
    if blocks:
        out |= {"decision": "block", "reason": "\n\n".join(blocks)}
    if gone:
        out["systemMessage"] = "\n".join(gone)
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    main()
