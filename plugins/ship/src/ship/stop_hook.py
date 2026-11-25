"""Stop hook: a session holding a feature (`hold.py`) can't end its turn until the gate settles.

Claude Code runs it whenever the agent is about to end its turn. For each branch this session holds,
it first asks T3 whether the branch's round still runs. If it does, the turn may end: the round's
completion wakes the session, and the first round tracks the branch as it runs, so nothing is judged
yet. Otherwise it judges the gate. OPEN or MERGED: the run is over, and the hold goes. Anything else:
it blocks the stop, and Claude Code hands the reason back to the agent, which goes on with what the
gate asks for: fix the open problems, review again, or leave on the record (`feature escalate`,
`feature release`).
"""

import json
import os
import sys
from pathlib import Path

from . import feature, github, hold, problems, t3
from .gitwiring import feature_issue


def _why(branch: str, issue: int, state: dict, blocking: list[dict], decision) -> str:
    # Short: it lands in the agent's context each time the hook fires. The gate and its reasons, the
    # open problems, and the way back to the run: the ship skill's loop says what each gate asks for.
    listed = "\n".join(f"  #{s['number']} [{problems.severity(s)}] {s['title']}" for s in blocking) or "  none"
    return (
        f"ship: this session is shipping `{branch}` (PR #{state['pr']}, feature #{issue}), no round of it is "
        f"running, and its gate is {decision}. Don't end the turn here.\n"
        f"Open problems:\n{listed}\n"
        f"{hold.work()}"
    )


def main() -> None:
    payload = json.load(sys.stdin)
    session = payload["session_id"]
    holds = hold.held(session)
    blocks: list[str] = []
    gone: list[str] = []
    failed: list[str] = []
    own_token = "GH_TOKEN" in os.environ
    for held in holds:
        branch, checkout = held["branch"], held["checkout"]
        if not Path(checkout).is_dir():
            hold.release(session, branch=branch, checkout=checkout)
            gone.append(f"ship: stopped holding `{branch}`: its checkout {checkout} is gone.")
            continue
        try:
            if t3.thread_running(held["round"]):
                continue
        except Exception as exc:  # T3 down: judged like a gate GitHub can't answer, below
            failed.append(
                f"ship couldn't ask T3 whether the round of `{branch}`, which this session holds, runs: {exc}"
            )
            continue
        os.chdir(checkout)
        if not own_token:
            # Holds may be in repos of different accounts; each picks its own (`use_owner_account`).
            os.environ.pop("GH_TOKEN", None)
        if feature_issue(branch) is None:
            # Proof the hold is obsolete: the branch isn't a tracked feature (any more).
            hold.release(session, branch=branch, checkout=checkout)
            gone.append(f"ship: stopped holding `{branch}`: it isn't a tracked feature in {checkout}.")
            continue
        try:
            github.use_owner_account()
            issue, state, blocking, decision = feature.gate_for(branch)
        except Exception as exc:  # one hold that can't be judged must not keep the others from being checked
            failed.append(f"ship couldn't judge the gate of `{branch}`, which this session holds: {exc}")
            continue
        if decision.settled:
            hold.release(session, branch=branch, checkout=checkout)
            continue
        blocks.append(_why(branch, issue, state, blocking, decision))
    # A gate that can't be judged (GitHub down) holds the stop once, so the agent retries; the next
    # stop in a row goes through, with the error shown, so an outage can't trap the session. The
    # hold stays: once the gate can be judged again, it holds as before.
    if failed and not payload.get("stop_hook_active"):
        blocks += [
            f"{why}\nRetry what you were doing; if the gate still can't be judged, say so and end the turn."
            for why in failed
        ]
    else:
        gone += failed
    out: dict = {}
    if blocks:
        out |= {"decision": "block", "reason": "\n\n".join(blocks)}
    if gone:
        out["systemMessage"] = "\n".join(gone)
    if out:
        print(json.dumps(out))


if __name__ == "__main__":
    main()
