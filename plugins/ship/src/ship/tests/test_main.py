"""Tests for the command-layer logic worth pinning: the merge close-out's ordering and wording."""

import pytest

from ship import __main__ as cli
from ship import github, problems
from ship.shell import CommandFailed
from ship.state import SCHEMA, new_state, render_block


def _problem(number: int, sev: str, *, state: str = "OPEN", extra: tuple[str, ...] = ()) -> dict:
    return {"number": number, "title": f"problem {number}", "state": state, "labels": [sev, *extra]}


def _merged_state() -> dict:
    state = new_state(branch="add-oauth", base="main", updated="2026-08-21T00:00:00Z")
    state["pr"] = 123
    state["status"] = "in-review"
    return state


def _stub_github(monkeypatch, *, calls: list[str], failing: str | None = None) -> None:
    def record(name):
        def fake(*_args, **_kwargs):
            calls.append(name)
            if name == failing:
                raise CommandFailed(1, ["gh"], "", "boom")
            return ""

        return fake

    monkeypatch.setattr(github, "comment", record("comment"))
    monkeypatch.setattr(github, "close_issue", record("close_issue"))
    monkeypatch.setattr(github, "set_issue_body", record("set_issue_body"))
    monkeypatch.setattr(github, "get_issue_body", lambda number: render_block(_merged_state()))


def test_record_outcome_closes_then_writes_state(monkeypatch):
    calls: list[str] = []
    _stub_github(monkeypatch, calls=calls)
    state = _merged_state()
    cli._record_outcome(42, state, "✅ Merged.", status="merged", already_closed=False)
    # The state write is LAST, and the close carries the note (one call, not close + comment).
    assert calls == ["close_issue", "set_issue_body"]
    assert state["status"] == "merged"


def test_record_outcome_only_comments_when_already_closed(monkeypatch):
    # The PR's `Closes #<issue>` reference usually closes the issue seconds before anyone runs the
    # close-out; `gh issue close` on a closed issue is not the way to find that out.
    calls: list[str] = []
    _stub_github(monkeypatch, calls=calls)
    cli._record_outcome(42, _merged_state(), "✅ Merged.", status="merged", already_closed=True)
    assert calls == ["comment", "set_issue_body"]


def test_record_outcome_leaves_state_unwritten_if_closing_fails(monkeypatch):
    # ORDER IS THE RETRY STRATEGY: a feature written as `merged` whose issue is still open would be
    # skipped by every future `feature reconcile` — orphaned permanently. Failing before the write
    # means a re-run does the whole thing again.
    calls: list[str] = []
    _stub_github(monkeypatch, calls=calls, failing="close_issue")
    state = _merged_state()
    with pytest.raises(CommandFailed):
        cli._record_outcome(42, state, "✅ Merged.", status="merged", already_closed=False)
    assert "set_issue_body" not in calls
    assert state["status"] == "in-review"


def test_reconcile_note_records_a_merge_with_no_open_problems():
    # Reconcile refuses while any problem is open, so the note it writes has none to name.
    note = cli._reconcile_note(123)
    assert "Merged in PR #123" in note and "No open problems" in note


def test_state_block_of_a_fresh_feature_is_current_schema():
    # Guards the assumption `reconcile` leans on when it reads a state block without a schema check.
    assert new_state(branch="b", base="main", updated="now")["schema"] == SCHEMA


def test_abandoned_note_never_claims_a_merge():
    note = cli._abandoned_note(123)
    # The work never landed: saying "merged" here would put a false claim in the permanent record.
    assert "Abandoned" in note
    assert "Merged" not in note


def _stub_adopt(monkeypatch, *, parent: str | None, pr: int | None, pr_base: str | None) -> dict:
    """Stub everything cmd_adopt touches; return what it did."""
    did: dict = {"parent": [], "issue": None}
    monkeypatch.setattr(cli, "feature_issue", lambda name: None)
    monkeypatch.setattr(cli, "parent_branch", lambda name: parent)
    monkeypatch.setattr(cli, "set_parent_branch", lambda name, base: did["parent"].append(base))

    def open_issue(name, base, title):
        did["issue"] = base
        return 7

    monkeypatch.setattr(cli, "_open_feature_issue", open_issue)
    monkeypatch.setattr(cli, "_load_state", lambda name: (7, new_state(branch=name, base="x", updated="t"), ""))
    monkeypatch.setattr(cli, "_save_state", lambda issue, body, state: None)
    monkeypatch.setattr(github, "open_pr_for_branch", lambda name: pr)
    monkeypatch.setattr(github, "pr_base_branch", lambda number: pr_base)
    monkeypatch.setattr(github, "set_pr_block", lambda number, quote, issue: True)
    monkeypatch.setattr(github, "comment", lambda issue, text: None)
    monkeypatch.setattr(github, "init_labels", lambda: None)
    return did


def _adopt_args(base: str | None = None):
    return cli.argparse.Namespace(branch="child", base=base, title=None)


def test_adopt_takes_the_pr_base_when_git_town_has_no_parent(monkeypatch):
    # A branch never stacked with git-town: assuming main would scope a stacked PR's review wrong.
    did = _stub_adopt(monkeypatch, parent=None, pr=25, pr_base="parent-x")
    cli.cmd_adopt(_adopt_args())
    assert did["parent"] == ["parent-x"]
    assert did["issue"] == "parent-x"


def test_adopt_refuses_a_pr_that_targets_another_branch_before_opening_an_issue(monkeypatch):
    did = _stub_adopt(monkeypatch, parent="main", pr=25, pr_base="parent-x")
    with pytest.raises(SystemExit, match="targets 'parent-x'"):
        cli.cmd_adopt(_adopt_args())
    assert did["issue"] is None


def test_adopt_without_a_pr_or_parent_bases_on_main(monkeypatch):
    did = _stub_adopt(monkeypatch, parent=None, pr=None, pr_base=None)
    cli.cmd_adopt(_adopt_args())
    assert did["parent"] == ["main"]


def test_merge_refuses_while_any_problem_is_open(monkeypatch):
    """Nothing ships as debt: a low problem, or one labelled by the old `follow-up`/`deferred`
    dispositions, holds the close-out until it is fixed or closed as wontfix."""
    state = _merged_state()
    blocking = problems.blocking(
        [
            _problem(5, "sev:low"),
            _problem(6, "sev:low", extra=("follow-up",)),
            _problem(7, "sev:high", extra=("deferred",)),
            _problem(8, "sev:low", state="CLOSED", extra=(problems.WONTFIX,)),
        ]
    )
    monkeypatch.setattr(cli, "_gate_for", lambda branch: (7, state, blocking, _decision(cli.gate.Verdict.MERGED)))
    with pytest.raises(SystemExit) as exc:
        cli.cmd_merge(cli.argparse.Namespace(branch="add-oauth", force=False))
    refusal = str(exc.value)
    assert "#5 [sev:low]" in refusal and "#6 [sev:low]" in refusal and "#7 [sev:high]" in refusal
    assert "#8" not in refusal
    assert "wontfix" in refusal and "follow-up" not in refusal


def _decision(verdict, reasons: list[str] | None = None) -> "cli.gate.GateDecision":
    return cli.gate.GateDecision(verdict=verdict, reasons=reasons or [], next_step="", rounds_used=1, budget=2)


def _stub_close_out(monkeypatch, decision, blocking: list[dict]) -> list[str]:
    notes: list[str] = []
    monkeypatch.setattr(cli, "_gate_for", lambda branch: (7, _merged_state(), blocking, decision))
    monkeypatch.setattr(github, "pr_state", lambda number: "OPEN")
    monkeypatch.setattr(github, "get_issue", lambda number: {"state": "OPEN"})
    monkeypatch.setattr(cli, "_record_outcome", lambda issue, state, note, **kw: notes.append(note))
    return notes


def test_merge_closes_out_a_merged_pr_whose_last_commits_were_never_reviewed(monkeypatch):
    """The gate's coverage rule held close-out hostage: the only ways past it were a review that
    could block nothing, or --force. A merged PR closes out, and the record names the unreviewed tail."""
    tail = "the last review looked at aaa111, but the branch is now at bbb222 — that code has never been reviewed"
    notes = _stub_close_out(monkeypatch, _decision(cli.gate.Verdict.MERGED, [tail]), [])
    cli.cmd_merge(cli.argparse.Namespace(branch="add-oauth", force=False))
    assert len(notes) == 1 and tail in notes[0]
    assert not notes[0].startswith("✅")


def test_merge_records_a_clean_merge(monkeypatch):
    notes = _stub_close_out(monkeypatch, _decision(cli.gate.Verdict.MERGED), [])
    cli.cmd_merge(cli.argparse.Namespace(branch="add-oauth", force=False))
    assert notes == ["✅ Merged. No open problems, and the last review covered it."]


def test_merge_still_refuses_a_pr_that_has_not_merged(monkeypatch):
    notes = _stub_close_out(monkeypatch, _decision(cli.gate.Verdict.OPEN), [])
    with pytest.raises(SystemExit, match="PR #123 is OPEN"):
        cli.cmd_merge(cli.argparse.Namespace(branch="add-oauth", force=False))
    assert notes == []


def test_round_finds_counts_by_severity_label():
    subs = [_problem(40, "sev:low"), _problem(41, "sev:high"), _problem(42, "sev:med"), _problem(43, "sev:med")]
    subs.append({"number": 44, "title": "unlabelled", "state": "OPEN", "labels": []})
    found, blocking, low = cli._round_finds(7, subs, [43, 41, 40, 44, 41])
    assert found == {"high": [41], "med": [43], "low": [40], "?": [44]}
    # Every one of them blocks the gate; the split is high/med (and unlabelled) vs low, for the trend.
    assert (blocking, low) == (3, 1)
    assert cli._round_finds(7, subs, []) == ({}, 0, 0)


def test_round_finds_refuses_a_number_that_is_not_one_of_the_problems():
    with pytest.raises(SystemExit, match="#99 is not one of its problem sub-issues"):
        cli._round_finds(7, [_problem(40, "sev:low")], [40, 99])


def _stub_gate_inputs(
    monkeypatch, *, pr_state: str, reviewed: str, branch_tip: str, pr_head: str, subs: list[dict] | None = None
) -> None:
    state = _merged_state()
    state["last_review"] = {"run": 1, "sha": reviewed, "new_blocking": 0, "new_low": 0, "regressions": 0, "summary": ""}
    state["review_history"] = [state["last_review"]]
    monkeypatch.setattr(cli, "_load_state", lambda branch: (7, state, ""))
    monkeypatch.setattr(github, "sub_issues", lambda issue: subs or [])
    monkeypatch.setattr(cli, "_resolve_budget", lambda state: (2, False, []))
    monkeypatch.setattr(github, "pr_state", lambda number: pr_state)
    monkeypatch.setattr(github, "pr_head_sha", lambda number: pr_head)
    monkeypatch.setattr(cli, "branch_head_sha", lambda branch: branch_tip)


def test_a_merged_pr_is_judged_by_what_merged_not_by_a_stale_local_branch(monkeypatch):
    """The fix was pushed from another checkout and reviewed; this checkout's branch still sits behind."""
    _stub_gate_inputs(monkeypatch, pr_state="MERGED", reviewed="fff666", branch_tip="aaa111", pr_head="fff666aaaa")
    _, _, _, decision = cli._gate_for("add-oauth")
    assert decision.verdict is cli.gate.Verdict.MERGED
    assert decision.reasons == []


def test_an_open_pr_is_still_judged_by_the_branch_tip(monkeypatch):
    _stub_gate_inputs(monkeypatch, pr_state="OPEN", reviewed="fff666", branch_tip="aaa111", pr_head="fff666aaaa")
    _, _, _, decision = cli._gate_for("add-oauth")
    assert decision.verdict is cli.gate.Verdict.REVIEW_AGAIN


def test_a_low_problem_left_open_keeps_the_gate_shut(monkeypatch):
    """The navari #4617 case: a review covered HEAD, only low problems were open, and the gate said
    OPEN with "four low problems ship as debt". Nothing ships as debt now."""
    _stub_gate_inputs(
        monkeypatch,
        pr_state="OPEN",
        reviewed="fff666",
        branch_tip="fff666",
        pr_head="fff666",
        subs=[_problem(4623, "sev:low"), _problem(4624, "sev:low", state="CLOSED", extra=(problems.WONTFIX,))],
    )
    _, _, blocking, decision = cli._gate_for("add-oauth")
    assert decision.verdict is not cli.gate.Verdict.OPEN
    assert [s["number"] for s in blocking] == [4623]
    assert "1 problem(s) open, every one blocking" in decision.reasons


def _stub_problem_edits(monkeypatch, sub: dict) -> list[str]:
    did: list[str] = []
    monkeypatch.setattr(cli, "_require_problem", lambda branch, number: sub)
    monkeypatch.setattr(github, "init_labels", lambda: did.append("init_labels"))
    monkeypatch.setattr(github, "add_label", lambda number, label: did.append(f"add {label}"))
    monkeypatch.setattr(github, "remove_label", lambda number, label: did.append(f"remove {label}"))
    monkeypatch.setattr(github, "comment", lambda number, body: did.append("comment"))
    monkeypatch.setattr(github, "reopen_issue", lambda number, body: did.append("reopen"))
    monkeypatch.setattr(
        github, "close_issue", lambda number, body, not_planned=False: did.append(f"close not_planned={not_planned}")
    )
    return did


def test_wontfix_creates_the_label_applies_it_and_closes_as_not_planned(monkeypatch):
    did = _stub_problem_edits(monkeypatch, _problem(8, "sev:low"))
    cli.cmd_problem_wontfix(cli.argparse.Namespace(branch="add-oauth", number=8, reason="by design"))
    # Label before close: if the close fails the problem is still open, and still blocks.
    assert did == ["init_labels", f"add {problems.WONTFIX}", "close not_planned=True"]


def test_wontfix_refuses_a_closed_problem(monkeypatch):
    did = _stub_problem_edits(monkeypatch, _problem(8, "sev:low", state="CLOSED"))
    with pytest.raises(SystemExit, match="already closed"):
        cli.cmd_problem_wontfix(cli.argparse.Namespace(branch="add-oauth", number=8, reason="by design"))
    assert did == []


def test_block_reopens_a_wontfix_problem_and_drops_the_label(monkeypatch):
    did = _stub_problem_edits(monkeypatch, _problem(8, "sev:low", state="CLOSED", extra=(problems.WONTFIX,)))
    cli.cmd_problem_block(cli.argparse.Namespace(branch="add-oauth", number=8, reason="rediscovered"))
    # Reopened before the label goes, so a failure in between leaves it open and blocking.
    assert did == ["reopen", f"remove {problems.WONTFIX}", "comment"]


def test_block_refuses_an_open_problem_which_already_blocks(monkeypatch):
    did = _stub_problem_edits(monkeypatch, _problem(8, "sev:low"))
    with pytest.raises(SystemExit, match="already blocks"):
        cli.cmd_problem_block(cli.argparse.Namespace(branch="add-oauth", number=8, reason="rediscovered"))
    assert did == []


def test_escalate_offers_what_the_gate_says_next(monkeypatch):
    """The comment used to hard-code the old options, deferral first, contradicting the gate."""
    posted: list[str] = []
    decision = cli.gate.GateDecision(
        verdict=cli.gate.Verdict.NEEDS_DECISION, reasons=[], next_step=cli.gate._NEXT_DECISION, rounds_used=2, budget=2
    )
    monkeypatch.setattr(cli, "_gate_for", lambda branch: (7, _merged_state(), [], decision))
    monkeypatch.setattr(github, "get_issue_body", lambda number: render_block(_merged_state()))
    monkeypatch.setattr(cli, "_save_state", lambda issue, body, state: None)
    monkeypatch.setattr(github, "comment", lambda number, body: posted.append(body))
    cli.cmd_escalate(cli.argparse.Namespace(branch="add-oauth", reason="budget spent"))
    assert "not fixes" in posted[0] and "ship the rest as debt" not in posted[0]
