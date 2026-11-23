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
