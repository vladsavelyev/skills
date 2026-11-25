import json

import pytest

from ship import hold


def test_the_same_branch_name_in_two_repos_is_two_holds():
    """#141: holding `fix-tests` in one repo overwrote the hold on another repo's `fix-tests`."""
    hold.hold("s1", branch="fix-tests", checkout="/app", round="t1")
    hold.hold("s1", branch="fix-tests", checkout="/lib", round="t2")
    assert hold.release("s1", branch="fix-tests", checkout="/app")
    assert hold.held("s1") == [hold.Hold(branch="fix-tests", checkout="/lib", round="t2")]
    assert not hold.holds("s1", branch="fix-tests", checkout="/app")


def test_releasing_the_last_hold_leaves_no_file_for_the_stop_hook_to_find():
    hold.hold("s1", branch="b", checkout="/app", round="t1")
    hold.release("s1", branch="b", checkout="/app")
    assert not hold.holds_dir().joinpath("s1.json").exists()


def test_the_held_text_names_the_ways_out_and_leaves_what_to_do_to_the_gate():
    """#153: a gate-specific text here told a redesign round to fix every open problem, against the
    gate's own next step. What this gate asks for is the gate's to say."""
    text = hold.work()
    assert "`feature escalate" in text and "`feature release" in text and "`Skill(ship:ship)`" in text
    assert "fix" not in text.lower()


def test_a_hold_written_before_rounds_had_threads_has_no_round_and_keeps_none_on_rewrite():
    path = hold.holds_dir() / "s1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([{"branch": "old", "checkout": "/app"}]))
    hold.hold("s1", branch="new", checkout="/app", round="t1")
    assert hold.held("s1") == [
        hold.Hold(branch="old", checkout="/app", round=None),
        hold.Hold(branch="new", checkout="/app", round="t1"),
    ]


@pytest.mark.parametrize(
    "entries",
    [
        {"branch": "b"},
        [{"branch": "b"}],
        [{"branch": "b", "checkout": "/app", "round": 3}],
        [{"branch": "b", "checkout": "/app", "x": 1}],
    ],
)
def test_a_file_that_isnt_holds_fails_loudly(entries):
    path = hold.holds_dir() / "s1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(entries))
    with pytest.raises(ValueError, match="not a"):
        hold.held("s1")
