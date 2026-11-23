"""Tests for problem classification — what does and doesn't hold the merge gate."""

from ship.problems import (
    HANDED_OFF,
    blocking,
    handed_off,
    is_blocking,
    recurring,
    repeated,
    severity,
    undecided,
    with_repeat,
)


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


def test_recurring_keeps_a_rounds_high_and_med_finds_filed_as_repeating_a_fixed_problem():
    """A low find repeating a fixed one is a nit back again, not evidence the design is wrong."""
    found = {"med": [42, 41], "low": [40], "?": [43]}
    # #40's body is never read: a low find can't recur.
    bodies = {41: with_repeat("repro", 31), 42: "repro", 43: with_repeat("repro", 33)}
    assert recurring(found, earlier=set(), body=bodies.__getitem__) == {"41": 31, "43": 33}
    assert recurring({}, earlier=set(), body=bodies.__getitem__) == {}


def test_a_repeat_counts_in_the_round_that_filed_it_and_a_re_find_is_a_regression():
    """navari #5176: #5279 was filed in round 3 as like #5216, then rediscovered in round 4, where it
    counts as a regression, not as a new problem of a kind already fixed."""
    bodies = {5301: with_repeat("repro", 5216)}
    assert recurring({"med": [5279, 5301]}, earlier={5279}, body=bodies.__getitem__) == {"5301": 5216}


def test_the_issue_body_carries_the_link():
    """#152: kept in the state too, a failed second write lost it for good. The issue is its one home."""
    assert repeated(with_repeat("repro", 10)) == 10
    assert with_repeat("repro", 10) == "repro\n\nRepeats #10: the same kind of bug, back after its fix."
    # Prose that mentions a repeat is not the link the CLI wrote.
    assert repeated("Repeats #10 maybe, but I'm not sure") is None
    assert repeated("repro") is None
