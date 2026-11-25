"""Holds: a Claude session working a feature's review loop can't end its turn until the gate settles.

A ship run used to end after one review round and hand the fixes to its caller as advice. On navari
PR #5355 the caller never fixed the problem the round filed, and the PR was admin-merged with it open.
Now the session driving the ship run holds the feature from its first round: the plugin's Stop hook
(`stop_hook.py`) refuses to let that session end its turn until the gate is OPEN or MERGED, except
while a round runs. Each round runs in a T3 child thread (`t3.py`), and `feature round` holds the
branch with that thread: while it runs, the session may end its turn and answer the user, and the
thread's completion wakes it to take in the report. A held session leaves the loop early in two
recorded ways only: `feature escalate` (the budget is spent with every problem closed, or a problem
needs the user's call) and `feature release` (the user stopped the run, or the review can't run).

One file per Claude session, under the user's state dir, listing the branches it holds, each with
its repo's main checkout (a branch name alone could be in two repos; the main checkout outlives
the feature's worktree) and the thread of its latest round. Keyed by session rather than by branch, so parallel sessions on other
features in the same repo never inherit each other's holds; kept outside the repo, so the Stop hook
finds it from whatever directory the session ends in, with one file check when nothing is held.
`bin/feature-stop-hook` reads the same directory before it starts Python.
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

# The id Claude Code gives every Bash command of a session, subagents and forked skills included;
# the Stop hook's input names the session the same way.
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"


def holds_dir() -> Path:
    state_home = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state_home) / "ship" / "holds"


def current_session() -> str | None:
    """The Claude session running this command, or None outside one (a person's own terminal, which
    has no turn to hold)."""
    return os.environ.get(SESSION_ENV)


def _path(session: str) -> Path:
    return holds_dir() / f"{session}.json"


@dataclass(frozen=True)
class Hold:
    branch: str
    checkout: str
    # The T3 thread of the branch's latest round. None: no round is in flight, so the gate is judged.
    round: str | None

    def same(self, branch: str, checkout: str) -> bool:
        # A branch name alone isn't a feature: two repos can each have a `fix-tests`.
        return self.branch == branch and self.checkout == checkout


def _decode(entry: object, path: Path) -> Hold:
    # Deliberately kept: files the plugin wrote before rounds ran in T3 threads have no `round`, and
    # stay on disk in sessions that held a branch then. They mean no round is in flight.
    if not (
        isinstance(entry, dict)
        and entry.keys() in ({"branch", "checkout"}, {"branch", "checkout", "round"})
        and isinstance(entry["branch"], str)
        and isinstance(entry["checkout"], str)
        and isinstance(entry.get("round"), str | None)
    ):
        raise ValueError(f"{path} holds {entry!r}, not a hold: {{branch, checkout[, round]}}")
    return Hold(branch=entry["branch"], checkout=entry["checkout"], round=entry.get("round"))


def held(session: str) -> list[Hold]:
    """The holds of `session`, oldest first."""
    path = _path(session)
    if not path.exists():
        return []
    entries = json.loads(path.read_text())
    if not isinstance(entries, list):
        raise ValueError(f"{path} holds {entries!r}, not a list of holds")
    return [_decode(entry, path) for entry in entries]


def _write(session: str, holds: list[Hold]) -> None:
    path = _path(session)
    if not holds:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(h) for h in holds], indent=2) + "\n")
    tmp.replace(path)


def hold(session: str, *, branch: str, checkout: str, round: str) -> None:
    """Hold `branch` of the repo whose main checkout is `checkout`, for `session`, while T3 thread
    `round` runs its round."""
    _write(
        session,
        [
            *(h for h in held(session) if not h.same(branch, checkout)),
            Hold(branch=branch, checkout=checkout, round=round),
        ],
    )


def holds(session: str, *, branch: str, checkout: str) -> bool:
    return any(h.same(branch, checkout) for h in held(session))


def release(session: str, *, branch: str, checkout: str) -> bool:
    """Drop `session`'s hold on `branch` of `checkout`'s repo. Returns whether there was one."""
    before = held(session)
    kept = [h for h in before if not h.same(branch, checkout)]
    _write(session, kept)
    return len(kept) != len(before)


def work() -> str:
    """What keeps a held session going, for the Stop hook: the ship run, and its two ways out on the
    record. What this gate asks for is the skill's loop and the gate's next
    step to say: a gate-specific text here drifted from them once (#153: a redesign round was told to
    fix every open problem), and restated the skill's loop each time the hook fired."""
    return (
        "Go on with the ship run (`Skill(ship:ship)`): its loop says what this gate asks for. Short of OPEN, "
        "leave only on the record: `feature escalate "
        "--reason '<…>' [--problem <#> …]` when the user must decide, `feature release --reason '<…>'` when they "
        "stopped the run or the review can't run."
    )
