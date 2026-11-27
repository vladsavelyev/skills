"""Tests for the merge-gate decision logic — the tri-state verdict, coverage, and churn signals."""

import pytest

from ship.gate import (
    _NEXT_DECISION,
    _NEXT_REDESIGN,
    _NEXT_REDESIGN_FAILED,
    EXIT_CODES,
    GateDecision,
    Verdict,
    covers_head,
    escalation_refusal,
    evaluate,
    needs_review,
    recurrences,
    redesign_chains,
    redesign_refusal,
    rising_reason,
    rounds_since_invalidation,
)

HEAD = "head99"


def run(
    n: int, *, blocking: int = 0, low: int = 0, regressed: list[int] = (), repeats: dict | None = None, sha: str = HEAD
) -> dict:
    entry = {
        "run": n,
        "sha": sha,
        "new_blocking": blocking,
        "new_low": low,
        "regressions": len(regressed),
        "regressed": list(regressed),
        "summary": f"run {n}",
    }
    # Rounds recorded before `repeats` existed have no such key, as most of these do.
    if repeats is not None:
        entry["repeats"] = repeats
    return entry


def gate(
    *,
    blocking_open,
    last_review,
    history,
    budget=3,
    unreviewed=(),
    answered=None,
    redesigned=None,
    dismissed=(),
    merged=False,
):
    """`answered` is the run a `feature budget --set` came after (None: the budget is auto-sized),
    `redesigned` each run a `feature redesign` answered with the chains it covered, and `dismissed`
    the problems closed as `wontfix`."""
    return evaluate(
        blocking_open=blocking_open,
        last_review=last_review,
        history=history,
        budget=budget,
        unreviewed=list(unreviewed),
        budget_answers=answered,
        redesigns=[{"run": n, "chains": chains, "reason": "r"} for n, chains in (redesigned or {}).items()],
        dismissed=frozenset(dismissed),
        pr_merged=merged,
    )


CLEAN = run(2)
DIRTY = run(1, blocking=2)


def test_open_when_nothing_blocking_and_the_review_covers_head():
    d = gate(blocking_open=0, last_review=CLEAN, history=[DIRTY, CLEAN])
    assert d.verdict is Verdict.OPEN
    assert d.open
    assert d.exit_code == 0
    assert d.reasons == []


def test_low_severity_findings_do_not_hold_the_gate():
    """The whole point: a run that found only nits is a clean pass."""
    nits_only = run(2, low=4)
    d = gate(blocking_open=0, last_review=nits_only, history=[nits_only])
    assert d.verdict is Verdict.OPEN


def test_closing_the_findings_as_wontfix_opens_the_gate_without_another_round():
    """No problem is open, and no code changed, so the review that found them still covers HEAD.

    The gate used to read `last_review["new_blocking"] != 0`, a count that went stale the moment a
    problem was closed without a commit, which left the gate shut with no way to reopen it.
    """
    found_two = run(2, blocking=2)
    d = gate(blocking_open=0, last_review=found_two, history=[run(1, blocking=2), found_two], budget=2)
    assert d.verdict is Verdict.OPEN


def test_commits_after_a_clean_review_close_the_gate_again():
    """Coverage, not counts: code nobody reviewed must not ship behind an older green light."""
    d = gate(blocking_open=0, last_review=CLEAN, history=[CLEAN], unreviewed=["newsha"])
    assert not d.open
    assert d.reasons == [f"1 commit(s) since the last review at {HEAD} have never been reviewed: newsha"]
    assert d.verdict is Verdict.REVIEW_AGAIN


def test_fixes_of_low_problems_and_changes_to_docs_and_tests_need_no_review():
    """navari #4890: a round found two low problems, their fix and a merge of the parent branch that
    resolved a conflict in a decision doc moved the head, and the gate asked for a full round, twice."""
    changes = [
        ("fix1", ["src/app.py"]),
        ("merge", ["docs/decisions/D-x.md"]),
        ("by-git", []),
        ("work", ["src/app.py", "tests/test_app.py"]),
        ("tests", ["src/ship/tests/test_app.py"]),
    ]
    assert needs_review(changes, low_fixes={"fix1"}, sensitive=[]) == ["work"]
    assert needs_review(changes, low_fixes=set(), sensitive=[]) == ["fix1", "work"]


def test_docs_on_a_sensitive_path_still_need_review():
    assert needs_review([("c", ["docs/auth.md"])], low_fixes=set(), sensitive=["docs/auth*"]) == ["c"]


def test_covers_head_matches_short_against_long_shas():
    assert covers_head({"sha": "abc1234"}, "abc1234def5678")
    assert covers_head({"sha": "abc1234def5678"}, "abc1234")
    assert not covers_head({"sha": "abc1234"}, "def5678")
    assert not covers_head(None, "abc1234")


def test_review_again_while_budget_remains():
    d = gate(blocking_open=2, last_review=DIRTY, history=[DIRTY])
    assert d.verdict is Verdict.REVIEW_AGAIN
    assert "2 problem(s) open, every one blocking" in d.reasons
    assert d.rounds_used == 1


def test_needs_decision_when_budget_spent():
    history = [run(1, blocking=3), run(2, blocking=1)]
    d = gate(blocking_open=1, last_review=history[-1], history=history, budget=2)
    assert d.verdict is Verdict.NEEDS_DECISION
    assert any("budget spent (2/2)" in r for r in d.reasons)
    assert d.next_step == _NEXT_DECISION


def test_needs_decision_on_churn_before_budget_is_spent():
    """Findings rising → escalate early rather than burn the remaining rounds."""
    history = [run(1, blocking=2), run(2, blocking=3)]
    d = gate(blocking_open=3, last_review=history[-1], history=history, budget=4)
    assert d.verdict is Verdict.NEEDS_DECISION
    assert any("rising" in r for r in d.reasons)
    # More findings may only mean a bigger PR than one round covers, so the choice stays open.
    assert d.next_step == _NEXT_DECISION


def test_buying_a_round_overrides_the_churn_escalation():
    """Churn escalates EARLY; a human who saw that and chose to spend more has answered it.

    Without this, `feature budget --set` — one of the two remedies the gate itself prints — was a
    silent no-op, because churn was re-derived from the same unchanged history and checked first.
    """
    history = [run(1, blocking=2), run(2, blocking=3)]
    stuck = gate(blocking_open=3, last_review=history[-1], history=history, budget=4)
    assert stuck.verdict is Verdict.NEEDS_DECISION
    bought = gate(blocking_open=3, last_review=history[-1], history=history, budget=4, answered=2)
    assert bought.verdict is Verdict.REVIEW_AGAIN


def test_a_pinned_budget_that_is_spent_still_needs_a_decision():
    history = [run(1, blocking=2), run(2, blocking=3)]
    d = gate(blocking_open=3, last_review=history[-1], history=history, budget=2, answered=2)
    assert d.verdict is Verdict.NEEDS_DECISION


def test_a_regression_is_a_fix_that_didnt_hold_so_it_asks_for_a_redesign():
    """The same bug back after its fix is the narrowest case of a kind of bug coming back."""
    history = [run(1, blocking=2), run(2, blocking=1, regressed=[7])]
    d = gate(blocking_open=1, last_review=history[-1], history=history, budget=4)
    assert d.verdict is Verdict.REDESIGN
    assert any("re-opened 1 fixed high/med problem(s) (#7)" in r for r in d.reasons)
    assert d.next_step == _NEXT_REDESIGN


def test_a_new_bug_of_a_kind_already_fixed_asks_for_a_redesign_not_another_round():
    """The fix patched one instance; the design still produces the kind, so the next round finds the next."""
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10})]
    d = gate(blocking_open=1, last_review=history[-1], history=history, budget=4)
    assert d.verdict is Verdict.REDESIGN
    assert any("#12 like #10" in r and "redesign" in r for r in d.reasons)
    assert d.next_step == _NEXT_REDESIGN


def test_the_redesign_step_points_to_an_arena_and_does_not_stop_to_ask():
    """navari-stack #23: nine problems sat open while the run waited for a yes to the redesign it
    recommended. A redesign the run believes in is its to make."""
    assert "competing designs" in _NEXT_REDESIGN and "critiqued blind" in _NEXT_REDESIGN
    assert "feature escalate" not in _NEXT_REDESIGN and "human" not in _NEXT_REDESIGN
    assert _NEXT_REDESIGN.index("Skill(ship:fix)") < _NEXT_REDESIGN.index("feature redesign")
    # The one exception: a redesign that crosses a one-way door is asked before it is built.
    assert "one-way door" in _NEXT_REDESIGN and "`Question:`" in _NEXT_REDESIGN


def test_a_redesign_answers_its_recurrence_and_its_review_round_follows():
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10})]
    d = gate(
        blocking_open=0,
        last_review=history[-1],
        history=history,
        budget=3,
        unreviewed=["redesign"],
        redesigned={2: [10]},
    )
    assert d.verdict is Verdict.REVIEW_AGAIN


def test_a_redesign_made_only_in_docs_is_still_reviewed():
    """A SKILL.md redesign is all docs, so `needs_review` drops its commits: the recurrence stays
    REDESIGN until it's recorded, and the round that reviews it is due all the same."""
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10})]
    assert gate(blocking_open=0, last_review=history[-1], history=history).verdict is Verdict.REDESIGN
    d = gate(blocking_open=0, last_review=history[-1], history=history, redesigned={2: [10]})
    assert d.verdict is Verdict.REVIEW_AGAIN
    assert "the redesign after run 2 has not been reviewed" in d.reasons


def test_a_clean_review_of_the_redesign_opens_the_gate():
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10}), run(3, sha=HEAD)]
    assert gate(blocking_open=0, last_review=history[-1], history=history, redesigned={2: [10]}).verdict is Verdict.OPEN


def test_the_kind_back_after_a_redesign_goes_to_a_human():
    """The run's own diagnosis missed: a second redesign on its say-so would be a loop."""
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10}), run(3, blocking=1, repeats={"20": 12})]
    d = gate(blocking_open=1, last_review=history[-1], history=history, budget=3, answered=2, redesigned={2: [10]})
    assert d.verdict is Verdict.NEEDS_DECISION
    assert d.next_step == _NEXT_REDESIGN_FAILED
    assert any("#20 like #12" in r and "already redesigned for that kind after run 2" in r for r in d.reasons)


def test_a_problem_of_the_redesigned_chain_back_again_goes_to_a_human():
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10}), run(3, blocking=1, regressed=[12])]
    d = gate(blocking_open=1, last_review=history[-1], history=history, budget=3, answered=2, redesigned={2: [10]})
    assert d.next_step == _NEXT_REDESIGN_FAILED


def test_an_unrelated_kind_back_after_a_redesign_gets_its_own_redesign():
    """#227: the redesign never addressed #30's kind, so its diagnosis can't have missed it."""
    history = [run(1, blocking=2), run(2, blocking=1, repeats={"12": 10}), run(3, blocking=1, repeats={"31": 30})]
    d = gate(blocking_open=1, last_review=history[-1], history=history, budget=4, answered=2, redesigned={2: [10]})
    assert d.verdict is Verdict.REDESIGN
    assert redesign_chains(history, dismissed=frozenset()) == [30]
    assert _NEXT_REDESIGN_FAILED.index("Skill(ship:fix)") < _NEXT_REDESIGN_FAILED.index("feature escalate")


def test_a_human_who_buys_a_round_after_a_failed_redesign_answers_it():
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10}), run(3, blocking=1, repeats={"20": 12})]
    d = gate(
        blocking_open=0,
        last_review=history[-1],
        history=history,
        budget=5,
        unreviewed=["x"],
        answered=3,
        redesigned={2: [10]},
    )
    assert d.verdict is Verdict.REVIEW_AGAIN


def test_a_round_whose_finds_repeat_nothing_is_not_a_recurrence():
    assert recurrences(run(2, blocking=2, repeats={}), dismissed=frozenset()) == {}
    # Rounds recorded before `repeats` existed have no links, so nothing recurs in them.
    assert recurrences(run(2, blocking=2), dismissed=frozenset()) == {}


def test_a_recurrence_chain_starts_at_the_first_problem_of_its_kind():
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10}), run(3, blocking=1, repeats={"20": 12})]
    assert redesign_chains(history, dismissed=frozenset()) == [10]
    assert redesign_chains(history, dismissed=frozenset({20})) == []


def test_only_the_last_round_decides_a_recurrence():
    """A human who saw an earlier recurrence and bought rounds answered it; a later clean round stands."""
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10}), run(3, blocking=1, repeats={})]
    assert gate(blocking_open=1, last_review=history[-1], history=history, budget=4).verdict is Verdict.REVIEW_AGAIN


def test_a_recurrence_outranks_a_spent_budget():
    """navari #5176 round 5: the budget said "spent", and the agent recommended one more round."""
    history = [run(1, blocking=2), run(2, blocking=2, repeats={"14": 10})]
    d = gate(blocking_open=2, last_review=history[-1], history=history, budget=2)
    assert d.next_step == _NEXT_REDESIGN
    assert not any("budget spent" in r for r in d.reasons)


def test_buying_a_round_after_a_recurrence_reviews_again():
    """The human chose: the redesign (or the patches) gets its review round."""
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10})]
    d = gate(blocking_open=0, last_review=history[-1], history=history, budget=3, unreviewed=["redesign"], answered=2)
    assert d.verdict is Verdict.REVIEW_AGAIN


def test_a_round_after_the_bought_one_is_judged_on_its_own():
    """The human answered round 2's recurrence; round 3's repeat is new evidence they haven't seen."""
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10}), run(3, blocking=1, repeats={"20": 12})]
    d = gate(blocking_open=1, last_review=history[-1], history=history, budget=5, answered=2)
    assert d.next_step == _NEXT_REDESIGN
    assert any("#20 like #12" in r for r in d.reasons)


def test_a_budget_pinned_before_any_round_answers_no_recurrence():
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10})]
    d = gate(blocking_open=1, last_review=history[-1], history=history, budget=4, answered=0)
    assert d.next_step == _NEXT_REDESIGN


def test_a_recurrence_closed_as_not_relevant_opens_the_gate():
    """The recurring finds were closed as `wontfix`: they weren't bugs, so nothing is left to redesign."""
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10})]
    assert gate(blocking_open=0, last_review=history[-1], history=history, dismissed=[12]).verdict is Verdict.OPEN


# navari #5176 ("checked or not"), rounds 1 and 2 as recorded, with the links the dedup step would have
# made: round 2's four high/med finds each say "checked, nothing found" of data the run didn't check,
# which is what round 1's three said too; #5218 is a low design finding and repeats nothing.
NAVARI_5176 = [
    {**run(1, blocking=3, sha="26a92daa2"), "found": {"high": [5201], "med": [5202, 5203]}},
    {
        **run(2, blocking=4, low=1, sha="cc507393", repeats={"5214": 5201, "5215": 5201, "5216": 5202, "5217": 5203}),
        "found": {"high": [5214], "med": [5215, 5216, 5217], "low": [5218]},
    },
]


def test_navari_5176_asks_for_a_redesign_at_round_2():
    """Every one of its five rounds found the same kind of bug somewhere new, and each time the gate's
    next step was to fix them all and buy another round. Round 2 is when the kind first came back."""
    d = gate(blocking_open=5, last_review=NAVARI_5176[-1], history=NAVARI_5176, budget=3)
    assert d.verdict is Verdict.REDESIGN
    assert d.next_step == _NEXT_REDESIGN
    assert any("#5214 like #5201, #5215 like #5201, #5216 like #5202, #5217 like #5203" in r for r in d.reasons)
    # Rising too (3 then 4), but the recurrence is the specific diagnosis, so it's the one named.
    assert not any("rising" in r for r in d.reasons)


def test_one_flat_round_is_not_churn():
    """1-then-1 is the commonest shape of a converging review; firing on it wasted the budget."""
    history = [run(1, blocking=1), run(2, blocking=1)]
    assert rising_reason(history) is None
    d = gate(blocking_open=1, last_review=history[-1], history=history, budget=4)
    assert d.verdict is Verdict.REVIEW_AGAIN


def test_a_clean_round_followed_by_a_dirty_one_is_not_churn():
    history = [run(1, blocking=0), run(2, blocking=1)]
    assert rising_reason(history) is None


def test_exit_codes_never_collide_with_command_failure_codes():
    """1 and 2 already mean "the command failed" (sys.exit(msg), argparse); verdicts must not."""
    codes = {v: EXIT_CODES[v] for v in Verdict}
    assert codes[Verdict.OPEN] == 0
    assert set(codes.values()).isdisjoint({1, 2})
    assert len(set(codes.values())) == len(codes)


def test_a_placeholder_round_spends_budget_but_not_the_trend():
    """Migrated features carry counted-but-detail-free rounds; the trend must ignore them."""
    placeholder = {"run": 1, "placeholder": True, "summary": "(pre-upgrade)"}
    history = [placeholder, run(2, blocking=3)]
    assert rising_reason(history) is None
    d = gate(blocking_open=3, last_review=history[-1], history=history, budget=2)
    # Two rounds are spent (the placeholder counts), so the budget check — not churn — escalates.
    assert d.rounds_used == 2
    assert d.verdict is Verdict.NEEDS_DECISION
    assert any("budget spent (2/2)" in r for r in d.reasons)


def test_converging_run_still_reviews_again():
    history = [run(1, blocking=5), run(2, blocking=2)]
    d = gate(blocking_open=2, last_review=history[-1], history=history, budget=4)
    assert d.verdict is Verdict.REVIEW_AGAIN


def test_closed_when_no_review_recorded():
    d = gate(blocking_open=0, last_review=None, history=[], budget=2)
    assert not d.open
    assert "no valid review run recorded" in d.reasons
    assert d.verdict is Verdict.REVIEW_AGAIN


def test_no_review_and_no_budget_left_needs_decision():
    """A sync-invalidated review with an exhausted budget is a human call, not a silent block."""
    history = [run(1, blocking=1), run(2, blocking=1)]
    d = gate(blocking_open=0, last_review=None, history=history, budget=2)
    assert d.verdict is Verdict.NEEDS_DECISION


def test_rounds_reset_after_a_base_change():
    """`feature sync` marks the history; rounds before the marker don't spend the new budget."""
    history = [run(1, blocking=2), run(2, blocking=2), {"event": "base-advanced", "sha": "abc"}]
    assert rounds_since_invalidation(history) == []
    d = gate(blocking_open=0, last_review=None, history=history, budget=2)
    assert d.verdict is Verdict.REVIEW_AGAIN
    assert d.rounds_used == 0


def test_churn_trend_ignores_rounds_before_a_base_change():
    history = [run(1, blocking=2), run(2, blocking=3), {"event": "base-advanced", "sha": "abc"}, run(3, blocking=3)]
    assert rising_reason(rounds_since_invalidation(history)) is None


def test_both_reasons_reported_together():
    d = gate(blocking_open=3, last_review=DIRTY, history=[DIRTY], unreviewed=["newsha"])
    assert len(d.reasons) == 2


def test_a_merged_pr_never_asks_for_another_review_but_names_what_was_outstanding():
    """An unreviewed tail on a merged PR used to read REVIEW_AGAIN, and an agent reviewed a merged PR."""
    d = gate(
        blocking_open=0,
        last_review=run(1, sha="old11"),
        history=[run(1, sha="old11")],
        unreviewed=["new22"],
        merged=True,
    )
    assert d.verdict is Verdict.MERGED
    assert d.exit_code == 30
    assert "ship:close" in d.next_step and "review" not in d.next_step.replace("Don't review", "")
    assert any("old11" in r and "never been reviewed" in r for r in d.reasons)


def test_merged_outranks_a_spent_budget():
    """Past the merge, escalating to a human about review rounds is as moot as another round."""
    history = [run(1, blocking=1), run(2, blocking=1)]
    d = gate(blocking_open=1, last_review=history[-1], history=history, budget=2, merged=True)
    assert d.verdict is Verdict.MERGED
    assert d.reasons == ["1 problem(s) open, every one blocking"]


def test_a_clean_merge_reports_nothing_outstanding():
    d = gate(blocking_open=0, last_review=CLEAN, history=[DIRTY, CLEAN], merged=True)
    assert d.verdict is Verdict.MERGED
    assert d.reasons == []


@pytest.mark.parametrize(
    ("verdict", "settled"),
    [(Verdict.OPEN, True), (Verdict.MERGED, True), (Verdict.REVIEW_AGAIN, False), (Verdict.NEEDS_DECISION, False)],
)
def test_a_ship_run_settles_only_once_the_pr_may_merge_or_has(verdict, settled):
    decision = GateDecision(verdict=verdict, reasons=[], next_step="", rounds_used=1, budget=2)
    assert decision.settled is settled


def _refusal(verdict, open_problems, named):
    decision = GateDecision(verdict=verdict, reasons=[], next_step="", rounds_used=2, budget=2)
    return escalation_refusal(decision, open_problems=open_problems, named=named)


def test_escalation_waits_until_every_problem_nobody_asked_about_is_closed():
    assert "#7 still open" in _refusal(Verdict.NEEDS_DECISION, [7, 8], [8])
    assert _refusal(Verdict.NEEDS_DECISION, [7, 8], [7, 8]) is None
    assert _refusal(Verdict.NEEDS_DECISION, [], []) is None


def test_escalation_refuses_a_number_that_is_not_an_open_problem():
    assert "#9 is not an open problem" in _refusal(Verdict.NEEDS_DECISION, [7], [7, 9])


def test_escalation_with_budget_left_is_only_for_a_problem_that_needs_the_user():
    assert "review budget remains" in _refusal(Verdict.REVIEW_AGAIN, [], [])
    assert _refusal(Verdict.REVIEW_AGAIN, [7], [7]) is None


def test_there_is_nothing_to_escalate_once_the_gate_is_open_or_the_pr_merged():
    assert "OPEN" in _refusal(Verdict.OPEN, [], [])
    assert "ship:close" in _refusal(Verdict.MERGED, [], [])


def test_a_redesign_is_not_escalated_but_made():
    """navari-stack #23: the run escalated the redesign it recommended, and waited for a yes."""
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10})]
    d = gate(blocking_open=1, last_review=history[-1], history=history)
    assert "#12 still open" in escalation_refusal(d, open_problems=[12], named=[])
    fixed = gate(blocking_open=0, last_review=history[-1], history=history, unreviewed=["redesign"])
    assert fixed.verdict is Verdict.REDESIGN
    assert "yours to make" in escalation_refusal(fixed, open_problems=[], named=[])
    assert escalation_refusal(d, open_problems=[12], named=[12]) is None


def test_a_redesign_is_recorded_once_its_problems_are_closed():
    history = [run(1, blocking=1), run(2, blocking=1, repeats={"12": 10})]
    d = gate(blocking_open=1, last_review=history[-1], history=history, unreviewed=["redesign"])
    assert "#12 still open" in redesign_refusal(d, open_problems=[12], commits=["c3"])
    assert redesign_refusal(d, open_problems=[], commits=["c3"]) is None
    assert "nothing was redesigned" in redesign_refusal(d, open_problems=[], commits=[])
    reviewing = gate(
        blocking_open=0, last_review=history[-1], history=history, unreviewed=["redesign"], redesigned={2: [10]}
    )
    assert "not REDESIGN" in redesign_refusal(reviewing, open_problems=[], commits=["c3"])
