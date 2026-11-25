import json

import pytest

from ship import hold


def test_the_same_branch_name_in_two_repos_is_two_holds():
    """#141: holding `fix-tests` in one repo overwrote the hold on another repo's `fix-tests`."""
    hold.hold("s1", branch="fix-tests", checkout="/app")
    hold.hold("s1", branch="fix-tests", checkout="/lib")
    assert hold.release("s1", branch="fix-tests", checkout="/app")
    assert hold.held("s1") == [hold.Hold(branch="fix-tests", checkout="/lib")]
    assert not hold.holds("s1", branch="fix-tests", checkout="/app")


def test_releasing_the_last_hold_leaves_no_file_for_the_stop_hook_to_find():
    hold.hold("s1", branch="b", checkout="/app")
    hold.release("s1", branch="b", checkout="/app")
    assert not hold.holds_dir().joinpath("s1.json").exists()


def test_the_held_text_names_the_ways_out_and_leaves_what_to_do_to_the_gate():
    """#153: a gate-specific text here told a redesign round to fix every open problem, against the
    gate's own next step. What this gate asks for is the gate's to say."""
    text = hold.work()
    assert "`feature escalate" in text and "`feature release" in text and "`Skill(ship:ship)`" in text
    assert "fix" not in text.lower()


@pytest.mark.parametrize(
    "entries",
    [
        {"branch": "b"},
        [{"branch": "b"}],
        [{"branch": "b", "checkout": "/app", "x": 1}],
    ],
)
def test_a_file_that_isnt_holds_fails_loudly(entries):
    path = hold.holds_dir() / "s1.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(entries))
    with pytest.raises(ValueError, match="not a"):
        hold.held("s1")


def _task(description: str, type: str = "subagent") -> dict:
    return {"id": "a1", "type": type, "status": "running", "description": description, "agent_type": "general-purpose"}


_FIX_IT = hold.Hold(branch="fix-it", checkout="/a/repo")


@pytest.mark.parametrize("kind", hold.ROUND_KINDS)
def test_a_round_of_the_hold_runs_while_its_subagent_is_in_flight(kind):
    assert _FIX_IT.round_running([_task(_FIX_IT.round_description(kind))])


@pytest.mark.parametrize(
    "tasks",
    [
        [],
        [_task(hold.Hold(branch="fix-it-2", checkout="/a/repo").round_description("review"))],
        [_task(hold.Hold(branch="fix-it", checkout="/b/repo").round_description("review"))],
        [_task("ship review"), _task("ship review fix-it")],
        [_task(_FIX_IT.round_description("review"), type="shell")],
    ],
)
def test_no_round_of_the_hold_runs_without_its_subagent(tasks):
    assert not _FIX_IT.round_running(tasks)
