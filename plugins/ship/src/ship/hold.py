"""Holds: a Claude session working a feature's review loop can't end its turn until the gate settles.

A ship run used to end after one review round and hand the fixes to its caller as advice. On navari
PR #5355 the caller never fixed the problem the round filed, and the PR was admin-merged with it open.
Now the session that records a review round, or closes one of its problems, holds the feature: the
plugin's Stop hook (`stop_hook.py`) refuses to let that session end its turn until the gate is OPEN
or MERGED. A held session leaves the loop early in two recorded ways only: `feature escalate` (the
budget is spent with every problem closed, or a problem needs the user's call) and `feature release`
(the user stopped the run, or the review can't run).

One file per Claude session, under the user's state dir, listing the branches it holds, each with
its repo's main checkout (a branch name alone could be in two repos; the main checkout outlives
the feature's worktree). Keyed by session rather than by branch, so parallel sessions on other
features in the same repo never inherit each other's holds; kept outside the repo, so the Stop hook
finds it from whatever directory the session ends in, with one file check when nothing is held.
`bin/feature-stop-hook` reads the same directory before it starts Python.
"""

import json
import os
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


def held(session: str) -> list[dict]:
    """The holds of `session`: `{"branch", "checkout"}` each, oldest first."""
    path = _path(session)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def _write(session: str, holds: list[dict]) -> None:
    path = _path(session)
    if not holds:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(holds, indent=2) + "\n")
    tmp.replace(path)


def _same(h: dict, branch: str, checkout: str) -> bool:
    # A branch name alone isn't a feature: two repos can each have a `fix-tests`.
    return h["branch"] == branch and h["checkout"] == checkout


def hold(session: str, *, branch: str, checkout: str) -> None:
    """Hold `branch` of the repo whose main checkout is `checkout`, for `session`."""
    _write(
        session,
        [*(h for h in held(session) if not _same(h, branch, checkout)), {"branch": branch, "checkout": checkout}],
    )


def holds(session: str, *, branch: str, checkout: str) -> bool:
    return any(_same(h, branch, checkout) for h in held(session))


def release(session: str, *, branch: str, checkout: str) -> bool:
    """Drop `session`'s hold on `branch` of `checkout`'s repo. Returns whether there was one."""
    before = held(session)
    kept = [h for h in before if not _same(h, branch, checkout)]
    _write(session, kept)
    return len(kept) != len(before)


def work(decision, worktree: str | None) -> str:
    """What a held session does until the gate `decision` settles, and its two ways out on the record.
    The Stop hook and `review record` both print this one text: kept apart, they drifted, and a
    redesign round was told to fix every open problem."""
    if decision.redesign:
        # Fixing first is the work a redesign throws away (`gate.escalation_refusal` lets it through).
        todo = (
            "The gate recommends a redesign over another round, so don't fix the open problems: the user decides first."
        )
    else:
        todo = (
            f"Fixing is part of the run: fix each problem at its root cause in {worktree or 'the feature worktree'}, "
            f"commit and push, `feature problem resolve <#> --commit <sha>` (or `feature problem wontfix <#> "
            f"--reason '<why>'` if it isn't relevant), and while the gate says REVIEW_AGAIN, review again with "
            f"`Skill(ship:ship)`."
        )
    return (
        f"{todo}\nOnly two ways out short of OPEN, both on the record: `feature escalate --reason '<…>' [--problem "
        f"<#> …]` when the user must decide (the budget is spent with every problem closed, a problem needs their "
        f"call, or the gate recommends a redesign), and `feature release --reason '<…>'` when the user stopped "
        f"the run or the review can't run."
    )
