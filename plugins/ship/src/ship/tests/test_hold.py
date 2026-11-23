from ship import hold


def test_the_same_branch_name_in_two_repos_is_two_holds():
    """#141: holding `fix-tests` in one repo overwrote the hold on another repo's `fix-tests`."""
    hold.hold("s1", branch="fix-tests", checkout="/app")
    hold.hold("s1", branch="fix-tests", checkout="/lib")
    assert hold.release("s1", branch="fix-tests", checkout="/app")
    assert hold.held("s1") == [{"branch": "fix-tests", "checkout": "/lib"}]
    assert not hold.holds("s1", branch="fix-tests", checkout="/app")


def test_releasing_the_last_hold_leaves_no_file_for_the_stop_hook_to_find():
    hold.hold("s1", branch="b", checkout="/app")
    hold.release("s1", branch="b", checkout="/app")
    assert not hold.holds_dir().joinpath("s1.json").exists()


def _decision(redesign: bool):
    from ship.gate import GateDecision, Verdict

    return GateDecision(
        Verdict.NEEDS_DECISION if redesign else Verdict.REVIEW_AGAIN, [], "next", 2, 4, redesign=redesign
    )


def test_a_held_session_fixes_the_problems_unless_the_gate_recommends_a_redesign():
    """Fixing first is the work a redesign throws away, so the user decides before anyone patches."""
    fixing = hold.work(_decision(redesign=False), "/wt")
    assert "fix each problem at its root cause in /wt" in fixing
    waiting = hold.work(_decision(redesign=True), "/wt")
    assert "don't fix the open problems" in waiting and "fix each problem" not in waiting
    for text in (fixing, waiting):
        assert "`feature escalate" in text and "`feature release" in text
