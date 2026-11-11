"""Tests for problem classification — what does and doesn't hold the merge gate."""

from ship.problems import HANDED_OFF, blocking, handed_off, is_blocking, severity, undecided


def sub(number: int, *, sev: str = "sev:high", state: str = "OPEN", extra: list[str] | None = None) -> dict:
    return {
        "number": number,
        "title": f"problem {number}",
        "state": state,
        "labels": ["problem", sev, *(extra or [])],
    }


def test_every_open_problem_blocks_at_every_severity():
    """Nothing ships as debt: a low-severity problem holds the gate like a high one."""
    assert is_blocking(sub(1, sev="sev:high"))
    assert is_blocking(sub(2, sev="sev:med"))
    assert is_blocking(sub(3, sev="sev:low"))


def test_closed_problems_never_block():
    assert not is_blocking(sub(1, state="CLOSED"))
    assert not is_blocking(sub(2, state="CLOSED", extra=["wontfix"]))


def test_labels_from_the_old_debt_dispositions_dont_unblock_an_open_problem():
    """`deferred` and `follow-up` once let a problem ship open; neither opens the gate now."""
    assert is_blocking(sub(1, sev="sev:high", extra=["deferred"]))
    assert is_blocking(sub(2, sev="sev:low", extra=["follow-up"]))


def test_a_problem_with_no_severity_label_blocks():
    unlabelled = {"number": 1, "title": "t", "state": "OPEN", "labels": ["problem"]}
    assert severity(unlabelled) == "sev:?"
    assert is_blocking(unlabelled)


def test_blocking_is_every_open_problem_and_drops_closed():
    subs = [
        sub(1, sev="sev:high"),
        sub(2, sev="sev:low"),
        sub(3, sev="sev:med", state="CLOSED"),
        sub(4, sev="sev:low", state="CLOSED", extra=["wontfix"]),
    ]
    assert [s["number"] for s in blocking(subs)] == [1, 2]


def test_a_handed_off_problem_still_blocks_but_no_longer_holds_the_close_out():
    subs = [sub(1, sev="sev:low"), sub(2, sev="sev:med", extra=[HANDED_OFF]), sub(3, state="CLOSED")]
    assert [s["number"] for s in blocking(subs)] == [1, 2]
    assert [s["number"] for s in undecided(subs)] == [1]
    assert [s["number"] for s in handed_off(subs)] == [2]


def test_an_old_follow_up_counts_as_handed_off_but_an_old_deferral_does_not():
    """`follow-up` was the old CLI's close-out decision on a merged PR; `deferred` was pre-merge debt."""
    subs = [sub(1, sev="sev:low", extra=["follow-up"]), sub(2, sev="sev:low", extra=["deferred"])]
    assert [s["number"] for s in undecided(subs)] == [2]
