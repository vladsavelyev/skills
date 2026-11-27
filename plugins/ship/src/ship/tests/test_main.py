"""Tests for the command-layer logic worth pinning: the merge close-out's ordering and wording."""

import fcntl
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from ship import __main__ as cli
from ship import feature, github, hold, problems
from ship.shell import CommandFailed
from ship.state import SCHEMA, StaleSchema, new_state, parse_block, render_block


def _problem(number: int, sev: str, *, state: str = "OPEN", extra: tuple[str, ...] = ()) -> dict:
    return {"number": number, "title": f"problem {number}", "state": state, "labels": [sev, *extra]}


def _merged_state() -> dict:
    state = new_state(branch="add-oauth", base="main", updated="2026-08-21T00:00:00Z")
    state["pr"] = 123
    state["status"] = "in-review"
    return state


def _stub_issue(monkeypatch, state: dict) -> dict:
    stored = {"body": render_block(state)}
    monkeypatch.setattr(github, "use_owner_account", lambda: None)
    monkeypatch.setattr(feature, "resolve_issue", lambda branch: 7)
    monkeypatch.setattr(github, "get_issue_body", lambda issue: stored["body"])
    monkeypatch.setattr(github, "set_issue_body", lambda issue, body: stored.update(body=body))
    return stored


def _stub_gate(monkeypatch, state: dict, blocking: list[dict], decision) -> None:
    monkeypatch.setattr(feature, "load_state", lambda branch, raw=False: (7, state, render_block(state)))
    monkeypatch.setattr(feature, "judge", lambda branch, issue, state: (blocking, decision))


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
    change = feature.Draft(42, _merged_state(), render_block(_merged_state()))
    cli._record_outcome(change, "✅ Merged.", status="merged", already_closed=False)
    # The state write is LAST, and the close carries the note (one call, not close + comment).
    assert calls == ["close_issue", "set_issue_body"]
    assert change.state["status"] == "merged"


def test_record_outcome_only_comments_when_already_closed(monkeypatch):
    # The PR's `Closes #<issue>` reference usually closes the issue seconds before anyone runs the
    # close-out; `gh issue close` on a closed issue is not the way to find that out.
    calls: list[str] = []
    _stub_github(monkeypatch, calls=calls)
    cli._record_outcome(
        feature.Draft(42, _merged_state(), render_block(_merged_state())),
        "✅ Merged.",
        status="merged",
        already_closed=True,
    )
    assert calls == ["comment", "set_issue_body"]


def test_record_outcome_leaves_state_unwritten_if_closing_fails(monkeypatch):
    # ORDER IS THE RETRY STRATEGY: a feature written as `merged` whose issue is still open would be
    # skipped by every future `feature reconcile` — orphaned permanently. Failing before the write
    # means a re-run does the whole thing again.
    calls: list[str] = []
    _stub_github(monkeypatch, calls=calls, failing="close_issue")
    change = feature.Draft(42, _merged_state(), render_block(_merged_state()))
    with pytest.raises(CommandFailed):
        cli._record_outcome(change, "✅ Merged.", status="merged", already_closed=False)
    assert "set_issue_body" not in calls


def test_reconcile_note_records_a_merge_with_no_open_problems():
    note = cli._reconcile_note(123, [])
    assert "Merged in PR #123" in note and "No open problems" in note


def test_reconcile_note_names_the_handed_off_problems():
    # Reconcile refuses while a problem is undecided, so every open one it gets here was handed off.
    note = cli._reconcile_note(123, [_problem(7, "sev:med", extra=(problems.HANDED_OFF,))])
    assert note.startswith("⚠️") and "Handed off, still open: #7 [sev:med]" in note


def test_state_block_of_a_fresh_feature_is_current_schema():
    # Guards the assumption `reconcile` leans on when it reads a state block without a schema check.
    assert new_state(branch="b", base="main", updated="now")["schema"] == SCHEMA


def test_abandoned_note_never_claims_a_merge():
    note = cli._abandoned_note(123, [])
    # The work never landed: saying "merged" here would put a false claim in the permanent record.
    assert "Abandoned" in note
    assert "Merged" not in note


def _stub_adopt(monkeypatch, *, parent: str | None, pr: int | None, pr_base: str | None) -> dict:
    """Stub everything cmd_adopt touches; return what it did."""
    did: dict = {"parent": [], "issue": None}
    monkeypatch.setattr(cli, "feature_issue", lambda name: None)
    monkeypatch.setattr(cli, "parent_branch", lambda name: parent)
    monkeypatch.setattr(cli, "set_parent_branch", lambda name, base: did["parent"].append(base))

    def open_issue(name, base, title, pr):
        did["issue"] = base
        state = new_state(branch=name, base=base, updated="t") | {"pr": pr}
        monkeypatch.setattr(github, "get_issue_body", lambda issue: render_block(state))
        return feature.Draft(7, state, render_block(state))

    monkeypatch.setattr(cli, "_open_feature_issue", open_issue)
    monkeypatch.setattr(github, "open_pr_for_branch", lambda name: pr)
    monkeypatch.setattr(github, "pr_base_branch", lambda number: pr_base)
    monkeypatch.setattr(github, "sub_issues", lambda issue: [])
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
    """A low problem, or one labelled `deferred` by the old CLI, holds the close-out until it is
    handed off or closed. An old `follow-up` was a close-out decision already, so it doesn't."""
    state = _merged_state()
    blocking = problems.blocking(
        [
            _problem(5, "sev:low"),
            _problem(6, "sev:low", extra=("follow-up",)),
            _problem(7, "sev:high", extra=("deferred",)),
            _problem(8, "sev:low", state="CLOSED", extra=(problems.WONTFIX,)),
        ]
    )
    _stub_gate(monkeypatch, state, blocking, _decision(cli.gate.Verdict.MERGED))
    with pytest.raises(SystemExit) as exc:
        cli.cmd_merge(cli.argparse.Namespace(branch="add-oauth", force=False))
    refusal = str(exc.value)
    assert "#5 [sev:low]" in refusal and "#7 [sev:high]" in refusal
    assert "#6" not in refusal and "#8" not in refusal
    assert "hand-off" in refusal and "wontfix" in refusal


def _decision(verdict, reasons: list[str] | None = None) -> "cli.gate.GateDecision":
    return cli.gate.GateDecision(verdict=verdict, reasons=reasons or [], next_step="", rounds_used=1, budget=2)


def _stub_close_out(monkeypatch, decision, blocking: list[dict]) -> list[str]:
    notes: list[str] = []
    _stub_gate(monkeypatch, _merged_state(), blocking, decision)
    monkeypatch.setattr(github, "pr_state", lambda number: "OPEN")
    monkeypatch.setattr(github, "get_issue", lambda number: {"state": "OPEN"})
    monkeypatch.setattr(cli, "_record_outcome", lambda change, note, **kw: notes.append(note))
    return notes


def test_merge_closes_out_a_merged_pr_whose_last_commits_were_never_reviewed(monkeypatch):
    """The gate's coverage rule held close-out hostage: the only ways past it were a review that
    could block nothing, or --force. A merged PR closes out, and the record names the unreviewed tail."""
    tail = "the last review looked at aaa111, but the branch is now at bbb222 — that code has never been reviewed"
    notes = _stub_close_out(monkeypatch, _decision(cli.gate.Verdict.MERGED, [tail]), [])
    cli.cmd_merge(cli.argparse.Namespace(branch="add-oauth", force=False))
    assert len(notes) == 1 and tail in notes[0]
    assert not notes[0].startswith("✅")


def test_merge_closes_out_with_handed_off_problems_and_names_them(monkeypatch):
    blocking = [_problem(5, "sev:low", extra=(problems.HANDED_OFF,))]
    reasons = ["1 problem(s) open, every one blocking"]
    notes = _stub_close_out(monkeypatch, _decision(cli.gate.Verdict.MERGED, reasons), blocking)
    cli.cmd_merge(cli.argparse.Namespace(branch="add-oauth", force=False))
    assert len(notes) == 1 and "Handed off, still open: #5 [sev:low]" in notes[0]
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
    monkeypatch.setattr(feature, "load_state", lambda branch, raw=False: (7, state, ""))
    monkeypatch.setattr(github, "sub_issues", lambda issue: subs or [])
    monkeypatch.setattr(feature, "resolve_budget", lambda state: (2, False, []))
    monkeypatch.setattr(github, "pr_state", lambda number: pr_state)
    monkeypatch.setattr(github, "pr_head_sha", lambda number: pr_head)
    monkeypatch.setattr(feature, "branch_head_sha", lambda branch: branch_tip)


def test_a_merged_pr_is_judged_by_what_merged_not_by_a_stale_local_branch(monkeypatch):
    """The fix was pushed from another checkout and reviewed; this checkout's branch still sits behind."""
    _stub_gate_inputs(monkeypatch, pr_state="MERGED", reviewed="fff666", branch_tip="aaa111", pr_head="fff666aaaa")
    _, _, _, decision = feature.gate_for("add-oauth")
    assert decision.verdict is cli.gate.Verdict.MERGED
    assert decision.reasons == []


def test_an_open_pr_is_still_judged_by_the_branch_tip(monkeypatch):
    _stub_gate_inputs(monkeypatch, pr_state="OPEN", reviewed="fff666", branch_tip="aaa111", pr_head="fff666aaaa")
    monkeypatch.setattr(feature, "has_object", lambda sha: True)
    monkeypatch.setattr(feature, "commits_since", lambda since, head: [("aaa111", ["fff666"])])
    monkeypatch.setattr(feature, "changed_paths", lambda old, new: ["src/app.py"])
    monkeypatch.setattr(feature, "sensitive_patterns", lambda: [])
    _, _, _, decision = feature.gate_for("add-oauth")
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
    _, _, blocking, decision = feature.gate_for("add-oauth")
    assert decision.verdict is not cli.gate.Verdict.OPEN
    assert [s["number"] for s in blocking] == [4623]
    assert "1 problem(s) open, every one blocking" in decision.reasons


def _stub_problem_edits(monkeypatch, sub: dict) -> list[str]:
    did: list[str] = []
    monkeypatch.setattr(feature, "snapshot", lambda branch: feature.Snapshot(7, _merged_state(), "", [sub]))
    monkeypatch.setattr(feature, "load_state", lambda branch, raw=False: (7, _merged_state(), ""))
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


def _stub_pr(monkeypatch, pr_state: str) -> None:
    monkeypatch.setattr(feature, "load_state", lambda branch, raw=False: (7, _merged_state(), ""))
    monkeypatch.setattr(github, "pr_state", lambda number: pr_state)


def test_hand_off_after_the_merge_records_the_reason_then_labels(monkeypatch):
    did = _stub_problem_edits(monkeypatch, _problem(8, "sev:low"))
    _stub_pr(monkeypatch, "MERGED")
    cli.cmd_problem_hand_off(cli.argparse.Namespace(branch="add-oauth", number=8, reason="fix in a separate PR"))
    # The label releases the close-out, so the reason goes on record first.
    assert did == ["init_labels", "comment", f"add {problems.HANDED_OFF}"]


@pytest.mark.parametrize("pr_state", ["OPEN", "CLOSED"])
def test_hand_off_refuses_unless_the_pr_merged(monkeypatch, pr_state):
    """Open: that would ship debt. Closed unmerged: its problems never landed, so they're wontfix."""
    did = _stub_problem_edits(monkeypatch, _problem(8, "sev:low"))
    _stub_pr(monkeypatch, pr_state)
    with pytest.raises(SystemExit, match="nothing ships as debt"):
        cli.cmd_problem_hand_off(cli.argparse.Namespace(branch="add-oauth", number=8, reason="later"))
    assert did == []


def test_block_reopens_a_wontfix_problem_and_drops_the_label(monkeypatch):
    did = _stub_problem_edits(monkeypatch, _problem(8, "sev:low", state="CLOSED", extra=(problems.WONTFIX,)))
    cli.cmd_problem_block(cli.argparse.Namespace(branch="add-oauth", number=8, reason="rediscovered"))
    # Reopened before the label goes, so a failure in between leaves it open and blocking.
    assert did == ["reopen", f"remove {problems.WONTFIX}", "comment"]


def test_block_finishes_when_the_label_removal_failed_after_the_reopen(monkeypatch):
    did = _stub_problem_edits(monkeypatch, _problem(8, "sev:low", extra=(problems.WONTFIX,)))
    cli.cmd_problem_block(cli.argparse.Namespace(branch="add-oauth", number=8, reason="rediscovered"))
    assert did == [f"remove {problems.WONTFIX}", "comment"]


def test_problem_list_marks_old_rejected_problems_wontfix_and_old_follow_ups_handed_off():
    assert "wontfix" in cli._problem_line(_problem(1, "sev:med", state="CLOSED", extra=("rejected",)))
    assert "fixed" in cli._problem_line(_problem(2, "sev:med", state="CLOSED"))
    assert "handed-off" in cli._problem_line(_problem(3, "sev:low", extra=("follow-up",)))


def test_block_leaves_an_open_problem_which_already_blocks(monkeypatch, capsys):
    did = _stub_problem_edits(monkeypatch, _problem(8, "sev:low"))
    cli.cmd_problem_block(cli.argparse.Namespace(branch="add-oauth", number=8, reason="rediscovered"))
    assert did == [] and "already blocks" in capsys.readouterr().out


def test_escalate_offers_what_the_gate_says_next(monkeypatch):
    """The comment used to hard-code the old options, deferral first, contradicting the gate."""
    posted: list[str] = []
    decision = cli.gate.GateDecision(
        verdict=cli.gate.Verdict.NEEDS_DECISION, reasons=[], next_step=cli.gate._NEXT_DECISION, rounds_used=2, budget=2
    )
    _stub_gate(monkeypatch, _merged_state(), [], decision)
    monkeypatch.setattr(github, "get_issue_body", lambda number: render_block(_merged_state()))
    monkeypatch.setattr(cli, "_save", lambda change, published=None: None)
    monkeypatch.setattr(github, "comment", lambda number, body: posted.append(body))
    cli.cmd_escalate(cli.argparse.Namespace(branch="add-oauth", reason="budget spent", problem=[]))
    assert "not fixes" in posted[0] and "ship the rest as debt" not in posted[0]


def _stub_escalate(
    monkeypatch, tmp_path, verdict, blocking: list[dict], decision: "cli.gate.GateDecision | None" = None
) -> list[tuple[int, str]]:
    posted: list[tuple[int, str]] = []
    decision = decision or cli.gate.GateDecision(verdict=verdict, reasons=[], next_step="next", rounds_used=2, budget=2)
    _stub_gate(monkeypatch, _merged_state(), blocking, decision)
    monkeypatch.setattr(github, "get_issue_body", lambda number: render_block(_merged_state()))
    monkeypatch.setattr(
        cli, "_save", lambda change, published=None: posted.append((change.issue, f"saved {change.state['status']}"))
    )
    monkeypatch.setattr(github, "comment", lambda number, body: posted.append((number, body)))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv(hold.SESSION_ENV, "s1")
    monkeypatch.setattr(cli, "main_checkout_root", lambda: "/repo")
    hold.hold("s1", branch="add-oauth", checkout="/repo")
    return posted


def test_escalate_refuses_while_a_problem_nobody_asked_about_is_open(monkeypatch, tmp_path):
    """Escalating ends a held ship run, so it must not be the way around fixing a problem."""
    posted = _stub_escalate(monkeypatch, tmp_path, cli.gate.Verdict.NEEDS_DECISION, [_problem(5400, "sev:med")])
    with pytest.raises(SystemExit, match="#5400 still open"):
        cli.cmd_escalate(cli.argparse.Namespace(branch="add-oauth", reason="budget spent", problem=[]))
    assert posted == []
    assert hold.held("s1") == [hold.Hold(branch="add-oauth", checkout="/repo")]


def test_escalate_refuses_while_review_budget_remains_and_no_problem_needs_the_user(monkeypatch, tmp_path):
    posted = _stub_escalate(monkeypatch, tmp_path, cli.gate.Verdict.REVIEW_AGAIN, [])
    with pytest.raises(SystemExit, match="review budget remains"):
        cli.cmd_escalate(cli.argparse.Namespace(branch="add-oauth", reason="tired", problem=[]))
    assert posted == []


def test_escalate_asks_on_each_named_problem_then_releases_the_session(monkeypatch, tmp_path):
    posted = _stub_escalate(monkeypatch, tmp_path, cli.gate.Verdict.REVIEW_AGAIN, [_problem(5400, "sev:med")])
    cli.cmd_escalate(cli.argparse.Namespace(branch="add-oauth", reason="keep v1 or drop it?", problem=[5400]))
    assert posted[0] == (5400, "⏸ Waiting on the user's call: keep v1 or drop it?")
    assert posted[1] == (7, "saved needs-decision")
    assert "Needs the user's call: #5400" in posted[2][1]
    assert hold.held("s1") == []


def test_release_puts_the_reason_on_the_tracking_issue_and_drops_the_hold(monkeypatch, tmp_path):
    posted = _stub_escalate(monkeypatch, tmp_path, cli.gate.Verdict.REVIEW_AGAIN, [_problem(5400, "sev:med")])
    cli.cmd_release(cli.argparse.Namespace(branch="add-oauth", reason="the reviewer is down"))
    assert posted[0][0] == 7 and "the reviewer is down" in posted[0][1] and "#5400 [sev:med]" in posted[0][1]
    assert hold.held("s1") == []


def test_release_refuses_a_branch_this_session_does_not_hold(monkeypatch, tmp_path):
    _stub_escalate(monkeypatch, tmp_path, cli.gate.Verdict.REVIEW_AGAIN, [])
    with pytest.raises(SystemExit, match="doesn't hold 'other'"):
        cli.cmd_release(cli.argparse.Namespace(branch="other", reason="x"))


def _in_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv(hold.SESSION_ENV, "s1")
    monkeypatch.setattr(cli, "main_checkout_root", lambda: "/repo")
    monkeypatch.setattr(github, "use_owner_account", lambda: None)


@pytest.mark.parametrize("kind", hold.ROUND_KINDS)
def test_a_round_holds_the_session_and_names_the_subagent_the_stop_hook_looks_for(monkeypatch, tmp_path, capsys, kind):
    _in_session(monkeypatch, tmp_path)
    cli.main(["round", kind, "--branch", "add-oauth"])
    assert hold.held("s1") == [hold.Hold(branch="add-oauth", checkout="/repo")]
    out = capsys.readouterr().out
    held = hold.Hold(branch="add-oauth", checkout="/repo")
    assert f"  {held.round_description(kind)}\n" in out
    task = {"type": "subagent", "description": out.splitlines()[1].strip()}
    assert held.round_running([task])


def test_the_next_round_keeps_one_hold(monkeypatch, tmp_path):
    _in_session(monkeypatch, tmp_path)
    cli.main(["round", "review", "--branch", "add-oauth"])
    cli.main(["round", "fix", "--branch", "add-oauth"])
    assert hold.held("s1") == [hold.Hold(branch="add-oauth", checkout="/repo")]


def test_a_round_outside_a_claude_session_refuses(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(github, "use_owner_account", lambda: None)
    with pytest.raises(SystemExit, match="isn't in one"):
        cli.main(["round", "review", "--branch", "add-oauth"])
    assert not (tmp_path / "ship").exists()


def test_closing_a_problem_holds_nobody(monkeypatch, tmp_path):
    _in_session(monkeypatch, tmp_path)
    monkeypatch.setattr(
        feature, "snapshot", lambda branch: feature.Snapshot(7, _merged_state(), "", [_problem(5, "sev:med")])
    )
    monkeypatch.setattr(feature, "load_state", lambda branch, raw=False: (7, _merged_state(), ""))
    monkeypatch.setattr(github, "init_labels", lambda: None)
    monkeypatch.setattr(github, "add_label", lambda number, label: None)
    monkeypatch.setattr(github, "close_issue", lambda number, note, not_planned=False: None)
    cli.cmd_problem_wontfix(cli.argparse.Namespace(branch="add-oauth", number=5, reason="by design"))
    assert hold.held("s1") == []


def test_resolve_records_its_fix_commits_before_closing(monkeypatch):
    """The gate reads a low problem's fix commits from the state block to let them through unreviewed."""
    did: list[str] = []
    _stub_issue(monkeypatch, _merged_state())
    monkeypatch.setattr(github, "sub_issues", lambda issue: [_problem(5, "sev:low")])
    monkeypatch.setattr(cli, "_reconcile_block", lambda branch, number, done: None)
    monkeypatch.setattr(cli, "full_sha", lambda rev: rev * 8)
    monkeypatch.setattr(cli, "_save", lambda change, published=None: did.append(f"save {change.state['fixes']}"))
    monkeypatch.setattr(github, "close_issue", lambda number, note: did.append(f"close {note}"))
    cli.cmd_problem_resolve(cli.argparse.Namespace(branch="feat", number=5, commit=["abc12", "def34"]))
    full = ["abc12" * 8, "def34" * 8]
    assert did == [f"save {{'{full[0]}': [5], '{full[1]}': [5]}}", f"close Fixed in {full[0][:9]}, {full[1][:9]}."]


def test_resolve_without_a_commit_records_no_fix(monkeypatch):
    did: list[str] = []
    _stub_issue(monkeypatch, _merged_state())
    monkeypatch.setattr(github, "sub_issues", lambda issue: [_problem(5, "sev:low")])
    monkeypatch.setattr(cli, "_reconcile_block", lambda branch, number, done: None)
    monkeypatch.setattr(cli, "_save", lambda change, published=None: pytest.fail("wrote the state"))
    monkeypatch.setattr(github, "close_issue", lambda number, note: did.append(note))
    cli.cmd_problem_resolve(cli.argparse.Namespace(branch="feat", number=5, commit=[]))
    assert did == ["Resolved."]


_RECORD = (
    "review record --sha abc --found 8 --summary s --risk low --door two-way "
    "--blast-radius b --complexity low --why w --branch add-oauth"
).split()


def _stub_review_record(monkeypatch, state: dict | None = None, subs: list[dict] | None = None) -> tuple[dict, list]:
    stored = _stub_issue(monkeypatch, state or _merged_state())
    did: list[str] = []
    monkeypatch.setattr(cli, "parent_branch", lambda branch: "main")
    monkeypatch.setattr(github, "pr_base_branch", lambda number: "main")
    monkeypatch.setattr(github, "sub_issues", lambda issue: subs or [_problem(8, "sev:med")])
    monkeypatch.setattr(github, "pr_diff_stats", lambda pr: (3, 40))
    monkeypatch.setattr(github, "set_pr_block", lambda pr, content, issue: did.append("block") or True)
    decision = cli.gate.GateDecision(cli.gate.Verdict.REVIEW_AGAIN, ["open"], "fix", 1, 2)
    monkeypatch.setattr(feature, "decide", lambda *a, **k: decision)
    monkeypatch.setattr(feature, "pr_merged", lambda state: False)
    monkeypatch.setattr(cli, "worktree_for_branch", lambda branch: None)
    monkeypatch.setattr(github, "comment", lambda issue, text: did.append("comment"))
    return stored, did


def _stub_problem_bodies(monkeypatch, bodies: dict[int, str]) -> None:
    """Each problem's body by its number; a problem not in `bodies` was filed as plain "repro"."""
    feature_body = github.get_issue_body
    monkeypatch.setattr(github, "get_issue_body", lambda n: feature_body(n) if n == 7 else bodies.get(n, "repro"))


def _changed_on_the_laptop() -> None:
    github.set_issue_body(7, render_block(parse_block(github.get_issue_body(7)) | {"last_prompt": "from the laptop"}))


def test_review_record_refuses_before_publishing_anything_when_the_state_changed_since_its_read(monkeypatch):
    _, did = _stub_review_record(monkeypatch)
    monkeypatch.setattr(github, "pr_base_branch", lambda number: _changed_on_the_laptop() or "main")
    with pytest.raises(SystemExit, match="so nothing was written"):
        cli.main(_RECORD)
    assert did == [] and parse_block(github.get_issue_body(7))["last_prompt"] == "from the laptop"


def test_review_record_says_what_went_out_when_the_state_changes_while_it_publishes(monkeypatch):
    _, did = _stub_review_record(monkeypatch)
    monkeypatch.setattr(github, "comment", lambda issue, text: did.append("comment") or _changed_on_the_laptop())
    with pytest.raises(
        SystemExit, match="the state wasn't written, though the PR's ship block and the round's comment"
    ):
        cli.main(_RECORD)
    assert did == ["block", "comment"] and parse_block(github.get_issue_body(7))["review_runs"] == 0


def test_recording_a_review_holds_nobody(monkeypatch, tmp_path):
    _in_session(monkeypatch, tmp_path)
    stored, _ = _stub_review_record(monkeypatch)
    cli.main(_RECORD)
    assert parse_block(stored["body"])["review_runs"] == 1 and hold.held("s1") == []


def _add_args(repeats: int | None = None):
    return cli.argparse.Namespace(
        branch="add-oauth",
        title="Says checked of what it didn't read",
        sev="high",
        body="repro",
        body_file=None,
        repeats=repeats,
    )


def _stub_problem_add(monkeypatch, repeated: dict | None) -> list[str]:
    filed: list[str] = []
    monkeypatch.setattr(feature, "resolve_issue", lambda branch: 7)
    subs = [] if repeated is None else [repeated]
    monkeypatch.setattr(feature, "snapshot", lambda branch: feature.Snapshot(7, _merged_state(), "", subs))
    monkeypatch.setattr(github, "set_issue_body", lambda issue, body: pytest.fail("wrote the state"))

    def add(parent, title, sev, detail):
        filed.append(detail)
        return 12

    monkeypatch.setattr(cli, "_add_problem", add)
    return filed


def test_problem_add_links_a_repeat_of_a_fixed_problem_in_the_issue_alone(monkeypatch):
    """#152: a second write to the state could fail after the issue was filed, losing the link."""
    filed = _stub_problem_add(monkeypatch, _problem(10, "sev:high", state="CLOSED"))
    cli.cmd_problem_add(_add_args(repeats=10))
    assert filed == [problems.with_repeat("repro", 10)]


def test_problem_add_without_repeats_files_the_body_as_given(monkeypatch):
    filed = _stub_problem_add(monkeypatch, None)
    cli.cmd_problem_add(_add_args())
    assert filed == ["repro"]


@pytest.mark.parametrize(
    ("repeated", "refusal"),
    [
        (_problem(10, "sev:high"), "#10 is still open"),
        (_problem(10, "sev:high", state="CLOSED", extra=(problems.WONTFIX,)), "#10 was closed as wontfix"),
        (_problem(10, "sev:high", state="CLOSED", extra=(problems.LEGACY_WONTFIX,)), "#10 was closed as wontfix"),
    ],
)
def test_problem_add_refuses_to_repeat_a_problem_that_was_never_fixed(monkeypatch, repeated, refusal):
    """Only a fix that didn't hold says the design keeps producing the bug. A finding like an open
    problem is more of the same before any fix; one like a wontfix answers a reason or isn't a bug."""
    filed = _stub_problem_add(monkeypatch, repeated)
    with pytest.raises(SystemExit, match=refusal):
        cli.cmd_problem_add(_add_args(repeats=10))
    assert filed == []


def test_review_record_keeps_the_rounds_finds_filed_as_repeating_a_fixed_problem(monkeypatch):
    state = _merged_state()
    # #50 repeats a fixed problem too, but an earlier round found it: it's back as a regression.
    state["review_history"] = [_round(1, {}) | {"found": {"med": [50]}}]
    state["review_runs"] = 1
    subs = [_problem(40, "sev:low"), _problem(41, "sev:high"), _problem(42, "sev:med"), _problem(50, "sev:med")]
    stored, _ = _stub_review_record(monkeypatch, state, subs)
    _stub_problem_bodies(monkeypatch, {40: problems.with_repeat("repro", 30), 41: problems.with_repeat("repro", 31)})
    found = _RECORD.index("8")
    cli.main([*_RECORD[:found], "40", "41", "42", "50", *_RECORD[found + 1 :], "--regressions", "50"])
    recorded = parse_block(stored["body"])["review_history"][-1]
    assert recorded["repeats"] == {"40": 30, "41": 31}
    assert recorded["regressed"] == [50] and recorded["regressions"] == 1


def test_a_low_find_bridges_the_recurrence_chain_its_redesign_covered(monkeypatch):
    """Round 2's #12 repeats #10 and is redesigned; round 3's low #14 repeats #12; round 4's #16
    repeats #14. That is the redesigned kind back, so a human decides."""
    state = _merged_state()
    state["review_history"] = [_round(1, {}) | {"found": {"med": [10]}}]
    state["review_runs"] = 1
    subs = [_problem(10, "sev:med", state="CLOSED"), _problem(12, "sev:med", state="CLOSED")]
    subs += [_problem(14, "sev:low", state="CLOSED"), _problem(16, "sev:med")]
    stored, _ = _stub_review_record(monkeypatch, state, subs)
    repeats = {12: 10, 14: 12, 16: 14}
    _stub_problem_bodies(monkeypatch, {n: problems.with_repeat("repro", r) for n, r in repeats.items()})
    found = _RECORD.index("8")
    for number in ("12", "14", "16"):
        cli.main([*_RECORD[:found], number, *_RECORD[found + 1 :]])
        if number == "12":
            state = parse_block(stored["body"])
            chains = cli.gate.redesign_chains(state["review_history"], dismissed=frozenset())
            cli.record_redesign(state, rounds=5, chains=chains, reason="r")
            stored["body"] = render_block(state)
    state = parse_block(stored["body"])
    history = state["review_history"]
    d = cli.gate.evaluate(
        blocking_open=1,
        last_review=history[-1],
        history=history,
        budget=5,
        unreviewed=[],
        budget_answers=2,
        redesigns=state["redesigns"],
    )
    assert d.verdict is cli.gate.Verdict.NEEDS_DECISION
    assert d.next_step == cli.gate._NEXT_REDESIGN_FAILED


@pytest.mark.parametrize("regressed", ["40", "9"])
def test_review_record_refuses_a_regression_that_is_not_a_high_or_med_find(monkeypatch, regressed):
    subs = [_problem(40, "sev:low"), _problem(41, "sev:high")]
    stored, did = _stub_review_record(monkeypatch, _merged_state(), subs)
    body = stored["body"]
    found = _RECORD.index("8")
    with pytest.raises(SystemExit, match=f"--regressions #{regressed} is not a high/med problem in --found"):
        cli.main([*_RECORD[:found], "40", "41", *_RECORD[found + 1 :], "--regressions", regressed])
    assert (did, stored["body"]) == ([], body)


def test_a_recurring_bug_is_redesigned_without_asking_and_its_review_round_bought(monkeypatch, tmp_path):
    """The run redesigns, closes the problems the redesign fixed, and records it, asking no one."""
    state = _merged_state()
    state["review_history"] = [_round(1, {}), _round(2, {"12": 10})]
    state["review_runs"], state["last_review"] = 2, state["review_history"][-1]
    subs = [_problem(12, "sev:high")]
    commits = []
    comments = []
    stored = _stub_issue(monkeypatch, state)
    monkeypatch.setattr(feature, "resolve_budget", lambda state: (state["review_budget"] or 2, False, []))
    monkeypatch.setattr(feature, "pr_merged", lambda state: False)
    monkeypatch.setattr(feature, "review_head", lambda branch, state, merged: "head")
    # The redesign is all docs (#228): `needs_review` leaves none of its commits for a review.
    monkeypatch.setattr(feature, "unreviewed", lambda state, head, subs: [])
    monkeypatch.setattr(cli, "commits_since", lambda since, head: [(sha, ["p"]) for sha in commits])
    monkeypatch.setattr(cli, "full_sha", lambda rev: "c3c3c3c3c3")
    monkeypatch.setattr(github, "pr_head_sha", lambda pr: "c3c3c3c3c3")
    monkeypatch.setattr(github, "sub_issues", lambda issue: subs)
    monkeypatch.setattr(github, "comment", lambda issue, text: comments.append(text))
    reason = "Every claim of checked covers unread data."
    redesign = ["redesign", "--branch", "add-oauth", "--reason", reason]

    with pytest.raises(SystemExit, match="#12 still open"):
        cli.main(redesign)
    subs[0] = _problem(12, "sev:high", state="CLOSED")
    assert feature.decide("add-oauth", state, subs, False).verdict is cli.gate.Verdict.REDESIGN
    with pytest.raises(SystemExit, match="nothing was redesigned"):
        cli.main(redesign)
    commits.append("c3c3c3c3c3")
    cli.main(redesign)

    state = parse_block(stored["body"])
    assert state["redesigns"] == [{"run": 2, "chains": [10], "reason": reason}] and state["review_budget"] == 3
    assert "Redesigned" in comments[-1] and "#12 like #10" in comments[-1] and "c3c3c3c3c" in comments[-1]
    decision = feature.decide("add-oauth", state, subs, False)
    assert decision.verdict is cli.gate.Verdict.REVIEW_AGAIN
    with pytest.raises(SystemExit, match="not REDESIGN"):
        cli.main(redesign)


def _stub_redesign(monkeypatch, *, pr_head: str = "c3c3c3c3c3") -> tuple[dict, list[str]]:
    """A feature whose round 2 repeated #10 as #12, #12 fixed by the redesign c3c3c3c3c3; returns its
    stored issue body, which each command reads afresh, and the comments posted."""
    state = _merged_state()
    state["review_history"] = [_round(1, {}), _round(2, {"12": 10})]
    state["review_runs"], state["last_review"] = 2, state["review_history"][-1]
    stored = _stub_issue(monkeypatch, state)
    comments: list[str] = []
    monkeypatch.setattr(github, "comment", lambda issue, text: comments.append(text))
    monkeypatch.setattr(github, "sub_issues", lambda issue: [_problem(12, "sev:high", state="CLOSED")])
    monkeypatch.setattr(github, "pr_head_sha", lambda pr: pr_head)
    monkeypatch.setattr(feature, "resolve_budget", lambda state: (state["review_budget"] or 2, False, []))
    monkeypatch.setattr(feature, "pr_merged", lambda state: False)
    monkeypatch.setattr(feature, "review_head", lambda branch, state, merged: "c3c3c3c")
    monkeypatch.setattr(feature, "unreviewed", lambda state, head, subs: ["c3c3c3c3c3"])
    monkeypatch.setattr(cli, "full_sha", lambda rev: "c3c3c3c3c3")
    monkeypatch.setattr(cli, "commits_since", lambda since, head: [("c3c3c3c3c3", ["p"])])
    return stored, comments


_REDESIGN = ["redesign", "--branch", "add-oauth", "--reason", "Every claim of checked covers unread data."]


def test_a_redesign_whose_comment_failed_is_recorded_by_the_retry(monkeypatch):
    """The retry posts the reason and records the redesign, once."""
    stored, comments = _stub_redesign(monkeypatch)

    def fail(issue, text):
        raise CommandFailed(1, ["gh"], "", "503")

    monkeypatch.setattr(github, "comment", fail)
    with pytest.raises(CommandFailed):
        cli.main(_REDESIGN)
    monkeypatch.setattr(github, "comment", lambda issue, text: comments.append(text))
    cli.main(_REDESIGN)
    assert len(comments) == 1 and "Every claim of checked covers unread data." in comments[0]
    assert [r["run"] for r in parse_block(stored["body"])["redesigns"]] == [2]


def test_a_redesign_is_recorded_only_once_it_is_pushed(monkeypatch):
    """#234: the round it buys reviews what the PR shows, not the local branch."""
    stored, comments = _stub_redesign(monkeypatch, pr_head="b2b2b2b2b2")
    with pytest.raises(SystemExit, match="Push it first"):
        cli.main(_REDESIGN)
    assert comments == [] and parse_block(stored["body"])["redesigns"] == []


def test_escalating_a_redesign_is_refused_as_the_runs_to_make(monkeypatch, tmp_path):
    history = [_round(1, {}), _round(2, {"12": 10})]
    fixed = cli.gate.evaluate(blocking_open=0, last_review=history[-1], history=history, budget=3, unreviewed=["c3"])
    posted = _stub_escalate(monkeypatch, tmp_path, fixed.verdict, [], decision=fixed)
    with pytest.raises(SystemExit, match="yours to make"):
        cli.cmd_escalate(cli.argparse.Namespace(branch="add-oauth", reason="Redesign it?", problem=[]))
    assert posted == []


def _round(run: int, repeats: dict) -> dict:
    return {
        "run": run,
        "sha": f"sha{run}",
        "new_blocking": 1,
        "new_low": 0,
        "regressions": 0,
        "summary": "",
        "found": {"high": [int(n) for n in repeats]},
        "repeats": repeats,
    }


def test_a_bought_round_answers_the_recurrence_it_saw_not_the_next_one(monkeypatch):
    """#151: `budget --set` used to switch the redesign signal off for every later round."""
    state = _merged_state()
    state["review_history"] = [_round(1, {}), _round(2, {"12": 10})]
    state["review_runs"], state["last_review"] = 2, state["review_history"][-1]
    stored = _stub_issue(monkeypatch, state)
    monkeypatch.setattr(github, "comment", lambda issue, text: None)
    cli.main(["budget", "--branch", "add-oauth", "--set", "4"])
    state = parse_block(stored["body"])
    subs = [_problem(12, "sev:high")]

    def decide():
        return feature.decide("add-oauth", state, subs, False, head=state["last_review"]["sha"])

    assert decide().verdict is cli.gate.Verdict.REVIEW_AGAIN
    state["review_history"].append(_round(3, {"20": 12}))
    state["review_runs"], state["last_review"] = 3, state["review_history"][-1]
    assert decide().next_step == cli.gate._NEXT_REDESIGN

    monkeypatch.setattr(feature, "resolve_budget", lambda state: (3, False, []))
    cli.main(["budget", "--branch", "add-oauth", "--auto"])
    assert parse_block(stored["body"])["review_budget_run"] is None


def test_reviewer_prints_both_reviewers_of_a_round(capsys):
    cli.main(["reviewer"])
    assert capsys.readouterr().out == (
        "Default reviewer: provider codex, model family sol\n"
        "/code-review reviewer: provider claudeAgent, model family opus, runs `/code-review high <PR URL> --max-findings all`\n"
    )


# Each model's `options` as `orchestrator_capabilities` listed them on 2026-11-26.
SOL = [
    {"id": "reasoningEffort", "values": ["low", "medium", "high", "xhigh", "max", "ultra"], "default": "low"},
    {"id": "serviceTier", "values": ["default", "priority"], "default": "default"},
]
FABLE = [
    {"id": "effort", "values": ["low", "medium", "high", "xhigh", "max", "ultrathink"], "default": "medium"},
    {"id": "ultracode", "values": [False, True]},
    {"id": "fastMode", "values": [False, True]},
]
BEDROCK_FABLE = [{"id": "effort", "values": ["default", "low", "medium", "high", "xhigh", "max"], "default": "default"}]


@pytest.mark.parametrize(
    ("options", "printed"),
    [
        (SOL, {"reasoningEffort": "high"}),
        # Claude rejects `reasoningEffort` ("Unknown option reasoningEffort; supported options: effort, …").
        (FABLE, {"effort": "high"}),
        (BEDROCK_FABLE, {"effort": "high"}),
    ],
)
def test_effort_runs_every_review_at_high_on_the_option_the_model_lists_for_it(capsys, options, printed):
    cli.main(["effort", "--model-options", json.dumps(options)])
    assert json.loads(capsys.readouterr().out) == printed


def test_effort_refuses_a_model_without_an_effort_option():
    no_effort = [{"id": "serviceTier", "values": ["default", "priority"]}]
    with pytest.raises(SystemExit, match="no option"):
        cli.main(["effort", "--model-options", json.dumps(no_effort)])


# navari-stack #23 in small: round 1's #30 is fixed; round 2 filed #36 (med) and #38 (low).
_ROUND_2 = "**Review round 2**. Findings: 1 med (#36), 1 low (#38)."


def _graded_state() -> dict:
    state = _merged_state()
    grade = {"risk": "med", "door": "two-way", "blast_radius": "b", "complexity": "med", "why": "w"}
    state["review_history"] = [
        {"run": 1, "sha": "a1", "found": {"med": [30]}, "new_blocking": 1, "regressions": 0},
        {"run": 2, "sha": "b2", "found": {"med": [36], "low": [38]}, "new_blocking": 1},
    ]
    state["review_history"][-1] |= {"new_low": 1, "regressions": 0, "summary": "", "grade": grade}
    state["review_runs"], state["last_review"] = 2, state["review_history"][-1]
    return state


def _stub_pr_block(monkeypatch, state: dict, subs: list[dict]) -> list[str]:
    """GitHub as `feature problem …` sees it on a PR whose round 2 filed `subs`, which closing,
    reopening and labelling a problem change in place; returns each block written."""
    blocks: list[str] = []

    def edit(number, change):
        at = next(i for i, s in enumerate(subs) if s["number"] == number)
        subs[at] = change(subs[at])

    monkeypatch.setattr(github, "use_owner_account", lambda: None)
    monkeypatch.setattr(feature, "resolve_issue", lambda branch: 7)
    _stub_issue(monkeypatch, state)
    monkeypatch.setattr(github, "sub_issues", lambda issue: list(subs))
    monkeypatch.setattr(github, "pr_diff_stats", lambda pr: (3, 40))
    monkeypatch.setattr(github, "set_pr_block", lambda pr, content, issue: blocks.append(content) or True)
    monkeypatch.setattr(
        github, "close_issue", lambda n, note, not_planned=False: edit(n, lambda s: s | {"state": "CLOSED"})
    )
    monkeypatch.setattr(github, "reopen_issue", lambda n, note: edit(n, lambda s: s | {"state": "OPEN"}))
    monkeypatch.setattr(github, "add_label", lambda n, label: edit(n, lambda s: s | {"labels": [*s["labels"], label]}))
    monkeypatch.setattr(
        github,
        "remove_label",
        lambda n, label: edit(n, lambda s: s | {"labels": [x for x in s["labels"] if x != label]}),
    )
    for name in ("init_labels", "comment"):
        monkeypatch.setattr(github, name, lambda *a, **k: None)
    monkeypatch.setattr(cli, "full_sha", lambda rev: rev * 8)
    return blocks


def _unavailable(pr, content, issue):
    raise CommandFailed(1, ["gh"], "", "503")


def test_a_round_is_recorded_without_its_line_while_its_problems_are_open(monkeypatch):
    state = _graded_state()
    state["review_history"].pop()
    state["review_runs"], state["last_review"] = 1, state["review_history"][-1]
    subs = [_problem(30, "sev:med", state="CLOSED"), _problem(8, "sev:med")]
    _stub_review_record(monkeypatch, state, subs)
    written: list[str] = []
    monkeypatch.setattr(github, "set_pr_block", lambda pr, content, issue: written.append(content) or True)
    cli.main(_RECORD)
    assert "Review round 1" in written[-1] and "Review round 2" not in written[-1]


def test_closing_a_rounds_last_open_problem_shows_its_line(monkeypatch):
    subs = [_problem(30, "sev:med", state="CLOSED"), _problem(36, "sev:med", state="CLOSED"), _problem(38, "sev:low")]
    blocks = _stub_pr_block(monkeypatch, _graded_state(), subs)
    cli.main(["problem", "wontfix", "38", "--reason", "by design", "--branch", "add-oauth"])
    assert _ROUND_2 in blocks[-1]


def test_resolving_a_rounds_last_open_problem_shows_its_line(monkeypatch):
    subs = [_problem(30, "sev:med", state="CLOSED"), _problem(36, "sev:med"), _problem(38, "sev:low", state="CLOSED")]
    blocks = _stub_pr_block(monkeypatch, _graded_state(), subs)
    cli.main(["problem", "resolve", "36", "--commit", "c3", "--branch", "add-oauth"])
    assert _ROUND_2 in blocks[-1]


def test_reopening_a_rounds_problem_takes_its_line_down(monkeypatch):
    subs = [_problem(30, "sev:med", state="CLOSED"), _problem(36, "sev:med", state="CLOSED")]
    subs.append(_problem(38, "sev:low", state="CLOSED"))
    blocks = _stub_pr_block(monkeypatch, _graded_state(), subs)
    cli.main(["problem", "block", "36", "--reason", "back again", "--branch", "add-oauth"])
    assert "Review round 2" not in blocks[-1] and "Review round 1" in blocks[-1]


def test_a_wontfix_rerun_after_its_block_write_failed_shows_the_line(monkeypatch):
    subs = [_problem(30, "sev:med", state="CLOSED"), _problem(36, "sev:med", state="CLOSED"), _problem(38, "sev:low")]
    blocks = _stub_pr_block(monkeypatch, _graded_state(), subs)
    monkeypatch.setattr(github, "set_pr_block", _unavailable)
    with pytest.raises(CommandFailed):
        cli.main(["problem", "wontfix", "38", "--reason", "by design", "--branch", "add-oauth"])
    monkeypatch.setattr(github, "set_pr_block", lambda pr, content, issue: blocks.append(content) or True)
    cli.main(["problem", "wontfix", "38", "--reason", "by design", "--branch", "add-oauth"])
    assert _ROUND_2 in blocks[-1]


def test_wontfix_still_refuses_a_problem_closed_as_fixed(monkeypatch):
    subs = [_problem(30, "sev:med", state="CLOSED"), _problem(36, "sev:med", state="CLOSED")]
    blocks = _stub_pr_block(monkeypatch, _graded_state(), subs)
    with pytest.raises(SystemExit, match="#36 is already closed"):
        cli.main(["problem", "wontfix", "36", "--reason", "by design", "--branch", "add-oauth"])
    assert blocks == []


def test_two_overlapping_closes_of_a_rounds_last_problems_show_its_line(monkeypatch, tmp_path):
    subs = [_problem(30, "sev:med", state="CLOSED"), _problem(36, "sev:med"), _problem(38, "sev:low")]
    blocks = _stub_pr_block(monkeypatch, _graded_state(), subs)
    add_label = github.add_label

    def resolve_on_the_laptop_meanwhile(number, label):
        with monkeypatch.context() as laptop:
            laptop.setenv("XDG_STATE_HOME", str(tmp_path / "laptop"))
            cli.main(["problem", "resolve", "36", "--commit", "c3", "--branch", "add-oauth"])
        add_label(number, label)

    monkeypatch.setattr(github, "add_label", resolve_on_the_laptop_meanwhile)
    cli.main(["problem", "wontfix", "38", "--reason", "by design", "--branch", "add-oauth"])
    assert _ROUND_2 not in blocks[0] and _ROUND_2 in blocks[-1]


def test_a_block_rerun_after_its_block_write_failed_takes_the_line_down(monkeypatch):
    """The reopen went through and the block write after it didn't, so the round's line still shows."""
    subs = [_problem(30, "sev:med", state="CLOSED"), _problem(36, "sev:med", state="CLOSED")]
    subs.append(_problem(38, "sev:low", state="CLOSED"))
    blocks = _stub_pr_block(monkeypatch, _graded_state(), subs)
    write = github.set_pr_block

    def unavailable_once_reopened(pr, content, issue):
        return _unavailable(pr, content, issue) if subs[1]["state"] == "OPEN" else write(pr, content, issue)

    monkeypatch.setattr(github, "set_pr_block", unavailable_once_reopened)
    with pytest.raises(CommandFailed):
        cli.main(["problem", "block", "36", "--reason", "back again", "--branch", "add-oauth"])
    monkeypatch.setattr(github, "set_pr_block", lambda pr, content, issue: blocks.append(content) or True)
    cli.main(["problem", "block", "36", "--reason", "back again", "--branch", "add-oauth"])
    assert "Review round 2" not in blocks[-1] and "Review round 1" in blocks[-1]


def test_a_block_written_while_a_problem_reopens_is_written_over(monkeypatch):
    """Another session renders the block from GitHub while #36 is still closed, and writes it as it reopens."""
    subs = [_problem(30, "sev:med", state="CLOSED"), _problem(36, "sev:med", state="CLOSED")]
    subs.append(_problem(38, "sev:low", state="CLOSED"))
    state = _graded_state()
    blocks = _stub_pr_block(monkeypatch, state, subs)
    reopen = github.reopen_issue
    concurrent: list[str] = []

    def reopen_meanwhile(number, note):
        concurrent.append(cli._block_content(feature.Snapshot(7, state, "", list(subs))))
        blocks.append(concurrent[-1])
        reopen(number, note)

    monkeypatch.setattr(github, "reopen_issue", reopen_meanwhile)
    cli.main(["problem", "block", "36", "--reason", "back again", "--branch", "add-oauth"])
    assert _ROUND_2 in concurrent[0]
    assert "Review round 2" not in blocks[-1]


def test_a_round_recorded_while_a_problem_closes_stays_in_the_block(monkeypatch):
    """#241: another session records round 3 while `wontfix` closes #38, so the block the wontfix
    writes is rendered from the feature as it is after the close, round 3 and all."""
    subs = [_problem(30, "sev:med", state="CLOSED"), _problem(36, "sev:med", state="CLOSED"), _problem(38, "sev:low")]
    state = _graded_state()
    blocks = _stub_pr_block(monkeypatch, state, subs)
    later = json.loads(json.dumps(state))
    round_3 = {"run": 3, "sha": "c3", "found": {}, "new_blocking": 0, "new_low": 0}
    later["review_history"].append(round_3 | {"regressions": 0, "regressed": [], "summary": "", "grade": {}})
    later["review_history"][-1]["grade"] = state["review_history"][-1]["grade"] | {"risk": "high"}
    later["review_runs"], later["last_review"] = 3, later["review_history"][-1]
    close = github.close_issue

    def close_meanwhile(number, note, not_planned=False):
        close(number, note, not_planned)
        github.set_issue_body(7, render_block(later))

    monkeypatch.setattr(github, "close_issue", close_meanwhile)
    cli.main(["problem", "wontfix", "38", "--reason", "by design", "--branch", "add-oauth"])
    assert "Review round 3" in blocks[-1] and _ROUND_2 in blocks[-1]


@pytest.mark.parametrize(
    ("command", "open_36", "went_out"),
    [
        (["problem", "resolve", "36", "--commit", "c3"], True, "the record of the fix and #36's close"),
        (["problem", "wontfix", "36", "--reason", "by design"], True, "#36's close"),
        (["problem", "block", "36", "--reason", "back again"], False, "#36's reopen"),
        (["pr", "123"], True, "the link to PR #123"),
    ],
)
def test_a_command_refuses_to_publish_the_block_over_a_change_made_while_it_renders_it(
    monkeypatch, command, open_36, went_out
):
    subs = [_problem(30, "sev:med", state="CLOSED"), _problem(36, "sev:med", state="OPEN" if open_36 else "CLOSED")]
    blocks = _stub_pr_block(monkeypatch, _graded_state(), subs)
    relinked = _graded_state() | {"pr": 124}
    monkeypatch.setattr(github, "pr_diff_stats", lambda pr: github.set_issue_body(7, render_block(relinked)) or (3, 40))
    with pytest.raises(SystemExit, match=re.escape(f"nothing more was written, though {went_out} went out")):
        cli.main([*command, "--branch", "add-oauth"])
    assert blocks == [] and parse_block(github.get_issue_body(7))["pr"] == 124


def test_closing_a_problem_no_round_found_leaves_the_block_alone(monkeypatch):
    """A failed check or a reviewer's comment, filed while babysitting: no round's line waits on it."""
    subs = [_problem(30, "sev:med", state="CLOSED"), _problem(50, "sev:med")]
    blocks = _stub_pr_block(monkeypatch, _graded_state(), subs)
    cli.main(["problem", "resolve", "50", "--commit", "c4", "--branch", "add-oauth"])
    assert blocks == []


def _stub_filing(monkeypatch, subs: list[dict]) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(github, "sub_issues", lambda parent: subs)
    monkeypatch.setattr(github, "unlink_sub_issue", lambda parent, child: calls.append(("unlink", child)))
    monkeypatch.setattr(github, "create_issue", lambda title, detail, labels: 200)
    monkeypatch.setattr(github, "link_sub_issue", lambda parent, child: calls.append(("link", child)))
    return calls


def test_add_problem_at_the_sub_issue_limit_unlinks_the_oldest_closed_one_first(monkeypatch):
    subs = [_problem(n, "sev:low", state="OPEN" if n % 2 else "CLOSED") for n in range(101, 101 + cli.SUB_ISSUE_LIMIT)]
    calls = _stub_filing(monkeypatch, subs)
    assert cli._add_problem(7, "t", "low", "d") == 200
    assert calls == [("unlink", 102), ("link", 200)]


def test_add_problem_under_the_limit_unlinks_nothing(monkeypatch):
    calls = _stub_filing(monkeypatch, [_problem(5, "sev:low", state="CLOSED")])
    cli._add_problem(7, "t", "low", "d")
    assert calls == [("link", 200)]


def test_add_problem_at_the_limit_with_every_problem_open_refuses_before_filing(monkeypatch):
    calls = _stub_filing(monkeypatch, [_problem(n, "sev:low") for n in range(cli.SUB_ISSUE_LIMIT)])
    with pytest.raises(SystemExit, match="GitHub's limit"):
        cli._add_problem(7, "t", "low", "d")
    assert calls == []


def test_two_commands_changing_one_feature_at_once_both_land(monkeypatch, lock_waited):
    stored = _stub_issue(monkeypatch, _merged_state())
    monkeypatch.setattr(github, "comment", lambda issue, text: None)
    first_read = threading.Event()
    load_state = feature.load_state

    def read_until_the_second_waits(branch, raw=False):
        read = load_state(branch, raw=raw)
        if not first_read.is_set():
            first_read.set()
            assert lock_waited.wait(timeout=10), "the second command didn't wait for the first"
        return read

    monkeypatch.setattr(feature, "load_state", read_until_the_second_waits)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(cli.main, ["prompt", "ship it", "--branch", "add-oauth"])
        assert first_read.wait(timeout=10)
        second = pool.submit(cli.main, ["budget", "--set", "4", "--branch", "add-oauth"])
        first.result()
        second.result()
    state = parse_block(stored["body"])
    assert (state["last_prompt"], state["review_budget"]) == ("ship it", 4)


def test_a_change_made_elsewhere_between_the_read_and_the_write_is_refused_not_undone(monkeypatch):
    stored = _stub_issue(monkeypatch, _merged_state())
    with feature.changing("add-oauth") as change:
        elsewhere = parse_block(stored["body"]) | {"last_prompt": "from the laptop"}
        github.set_issue_body(7, render_block(elsewhere))
        change.state["review_budget"] = 4
        with pytest.raises(feature.StateChanged, match="nothing was written"):
            cli._save(change)
    assert parse_block(stored["body"])["last_prompt"] == "from the laptop"


def test_the_write_keeps_an_edit_to_the_prose_around_the_block(monkeypatch):
    stored = _stub_issue(monkeypatch, _merged_state())
    with feature.changing("add-oauth") as change:
        stored["body"] = "Notes added by hand.\n\n" + stored["body"]
        change.state["review_budget"] = 4
        cli._save(change)
    assert stored["body"].startswith("Notes added by hand.") and parse_block(stored["body"])["review_budget"] == 4


class _Repo:
    def __init__(self, monkeypatch, state: dict, prs: dict[int, str]):
        self.issue = {"number": 7, "title": "A feature", "state": "OPEN", "body": render_block(state)}
        self.prs = prs
        self.successor: int | None = None
        self.notes: list[str] = []
        self.blocks: list[int] = []
        monkeypatch.setattr(github, "use_owner_account", lambda: None)
        monkeypatch.setattr(github, "feature_issues", lambda: [dict(self.issue)])
        monkeypatch.setattr(github, "get_issue", lambda number: dict(self.issue))
        monkeypatch.setattr(github, "get_issue_body", lambda number: self.issue["body"])
        monkeypatch.setattr(github, "set_issue_body", lambda number, body: self.issue.update(body=body))
        monkeypatch.setattr(github, "pr_states", lambda numbers: {n: self.prs[n] for n in numbers})
        monkeypatch.setattr(github, "pr_state", lambda number: self.prs[number])
        monkeypatch.setattr(github, "sub_issues", lambda number: [])
        monkeypatch.setattr(github, "open_pr_for_branch", lambda branch: self.successor)
        monkeypatch.setattr(github, "comment", lambda number, note: self.notes.append(note))
        monkeypatch.setattr(github, "close_issue", self._close)
        monkeypatch.setattr(github, "set_pr_block", lambda pr, content, issue: self.blocks.append(pr) or True)

    def _close(self, number, note, not_planned=False):
        self.notes.append(note)
        self.issue["state"] = "CLOSED"

    @property
    def state(self) -> dict:
        return parse_block(self.issue["body"])

    def change(self, **fields) -> None:
        self.issue["body"] = render_block(self.state | fields)

    def before_lock(self, monkeypatch, other) -> None:
        locked = feature.locked

        @contextmanager
        def after_other(branch):
            other()
            with locked(branch):
                yield

        monkeypatch.setattr(feature, "locked", after_other)


def test_reconcile_closes_out_a_feature_whose_pr_merged(monkeypatch):
    repo = _Repo(monkeypatch, _merged_state(), {123: "MERGED"})
    cli.main(["reconcile"])
    assert (repo.state["status"], repo.issue["state"]) == ("merged", "CLOSED")
    assert "Merged in PR #123" in repo.notes[-1]


def test_reconcile_closes_a_feature_whose_pr_closed_unmerged_as_abandoned(monkeypatch):
    repo = _Repo(monkeypatch, _merged_state(), {123: "CLOSED"})
    cli.main(["reconcile"])
    assert (repo.state["status"], repo.issue["state"]) == ("abandoned", "CLOSED")
    assert "Abandoned" in repo.notes[-1]


def test_reconcile_repairs_the_block_of_a_feature_whose_pr_is_open(monkeypatch):
    repo = _Repo(monkeypatch, _merged_state(), {123: "OPEN"})
    cli.main(["reconcile"])
    assert repo.blocks == [123] and repo.state["status"] == "in-review" and repo.notes == []


def test_reconcile_leaves_an_open_prs_block_that_needs_no_repair_uncounted(monkeypatch, capsys):
    repo = _Repo(monkeypatch, _merged_state(), {123: "OPEN"})
    monkeypatch.setattr(github, "set_pr_block", lambda pr, content, issue: repo.blocks.append(pr) and False)
    cli.main(["reconcile"])
    assert capsys.readouterr().out == (
        "Scanned 1 feature issue(s): closed out 0 merged, closed 0 abandoned, repaired 0, 0 needing a human decision.\n"
    )


def test_reconcile_hands_back_a_feature_whose_branch_has_a_new_pr(monkeypatch, capsys):
    repo = _Repo(monkeypatch, _merged_state(), {123: "CLOSED"})
    repo.successor = 130
    cli.main(["reconcile"])
    assert (repo.state["status"], repo.issue["state"], repo.notes) == ("in-review", "OPEN", [])
    assert capsys.readouterr().out == (
        "#7 (A feature): PR #123 was closed unmerged, but branch 'add-oauth' has open PR #130 — run `feature pr 130`\n"
        "Scanned 1 feature issue(s): closed out 0 merged, closed 0 abandoned, repaired 0, 1 needing a human decision.\n"
    )


def test_reconcile_leaves_a_merged_feature_with_an_undecided_problem_for_a_human(monkeypatch, capsys):
    repo = _Repo(monkeypatch, _merged_state(), {123: "MERGED"})
    monkeypatch.setattr(github, "sub_issues", lambda number: [_problem(8, "sev:med")])
    cli.main(["reconcile"])
    assert (repo.state["status"], repo.issue["state"], repo.notes) == ("in-review", "OPEN", [])
    out = capsys.readouterr().out
    assert "#7 (A feature): PR #123 MERGED, but 1 open problem(s) need a decision first: #8 [sev:med]" in out
    assert "1 needing a human decision." in out


def test_reconcile_upgrades_a_schema_1_block_as_it_closes_the_feature_out(monkeypatch, capsys):
    repo = _Repo(monkeypatch, _merged_state() | {"schema": 1, "last_review": None}, {123: "MERGED"})
    cli.main(["reconcile"])
    assert (repo.state["schema"], repo.state["status"], repo.issue["state"]) == (SCHEMA, "merged", "CLOSED")
    assert "#7 (A feature): closed out — merged in PR #123 + state upgraded from schema 1\n" in capsys.readouterr().out


def test_reconcile_reports_a_schema_1_block_it_cannot_upgrade(monkeypatch, capsys):
    state = _merged_state() | {"schema": 1}
    del state["last_review"]
    repo = _Repo(monkeypatch, state, {123: "MERGED"})
    with pytest.raises(SystemExit, match="Fix those by hand"):
        cli.main(["reconcile"])
    assert (repo.issue["state"], repo.notes) == ("OPEN", [])
    assert "#7: could not migrate its schema-1 state block" in capsys.readouterr().out


def test_reconcile_leaves_a_feature_another_command_closed_out_meanwhile(monkeypatch, capsys):
    repo = _Repo(monkeypatch, _merged_state(), {123: "MERGED"})
    repo.before_lock(monkeypatch, lambda: repo.change(status="merged"))
    cli.main(["reconcile"])
    assert repo.notes == [] and repo.issue["state"] == "OPEN"
    assert "changed by another command meanwhile" in capsys.readouterr().out


def test_reconcile_leaves_a_feature_relinked_to_a_new_pr_meanwhile(monkeypatch, capsys):
    repo = _Repo(monkeypatch, _merged_state(), {123: "MERGED", 124: "OPEN"})
    repo.before_lock(monkeypatch, lambda: repo.change(pr=124))
    cli.main(["reconcile"])
    assert (repo.state["pr"], repo.state["status"], repo.issue["state"]) == (124, "in-review", "OPEN")
    assert "changed by another command meanwhile" in capsys.readouterr().out


def test_reconcile_refuses_a_schema_it_never_wrote_that_a_block_got_meanwhile(monkeypatch, capsys):
    repo = _Repo(monkeypatch, _merged_state(), {123: "OPEN"})
    repo.before_lock(monkeypatch, lambda: repo.change(schema=99))
    with pytest.raises(SystemExit, match="Fix those by hand"):
        cli.main(["reconcile"])
    assert repo.blocks == [] and "#7: state block has unknown schema 99" in capsys.readouterr().out


def test_reconcile_reports_a_block_mangled_meanwhile_as_unreadable(monkeypatch, capsys):
    repo = _Repo(monkeypatch, _merged_state(), {123: "MERGED"})
    repo.before_lock(monkeypatch, lambda: repo.issue.update(body="The block was deleted by hand."))
    with pytest.raises(SystemExit, match="Fix those by hand"):
        cli.main(["reconcile"])
    assert repo.notes == [] and "1 feature issue(s) could not be reconciled" in capsys.readouterr().out


def test_reconcile_refuses_to_write_over_a_change_made_while_it_closes_out(monkeypatch):
    repo = _Repo(monkeypatch, _merged_state(), {123: "MERGED"})
    close = repo._close

    def close_while_the_laptop_writes(number, note, not_planned=False):
        repo.change(last_prompt="from the laptop")
        close(number, note, not_planned)

    monkeypatch.setattr(github, "close_issue", close_while_the_laptop_writes)
    with pytest.raises(SystemExit, match="though the outcome's comment and the issue's close went out"):
        cli.main(["reconcile"])
    assert (repo.state["last_prompt"], repo.state["status"]) == ("from the laptop", "in-review")


def test_reconcile_refuses_to_repair_the_block_over_a_change_made_while_it_renders_it(monkeypatch):
    repo = _Repo(monkeypatch, _merged_state(), {123: "OPEN"})
    monkeypatch.setattr(github, "sub_issues", lambda number: repo.change(pr=124) or [])
    with pytest.raises(SystemExit, match="nothing was written"):
        cli.main(["reconcile"])
    assert repo.blocks == []


def test_describe_refuses_to_publish_over_a_change_made_while_it_renders_the_block(monkeypatch, tmp_path):
    _stub_issue(monkeypatch, _merged_state())
    relinked = _merged_state() | {"pr": 124}
    monkeypatch.setattr(github, "sub_issues", lambda issue: github.set_issue_body(7, render_block(relinked)) or [])
    monkeypatch.setattr(github, "pr_diff_stats", lambda pr: (3, 40))
    published: list[int] = []
    monkeypatch.setattr(github, "set_pr_description", lambda pr, *args: published.append(pr))
    (tmp_path / "body.md").write_text("Why it changed.")
    with pytest.raises(SystemExit, match="nothing was written"):
        cli.main(["describe", "--body-file", str(tmp_path / "body.md"), "--branch", "add-oauth"])
    assert published == []


@pytest.mark.parametrize(
    ("pr_state", "said"),
    [
        ("MERGED", "#7 (A feature): PR #123 MERGED, status 'in-review' → would close out\n"),
        ("CLOSED", "#7 (A feature): PR #123 CLOSED, status 'in-review' → would close as abandoned\n"),
        ("OPEN", "#7 (A feature): PR #123 open → block repaired if stale\n"),
    ],
)
def test_a_dry_run_reconcile_says_what_it_would_do_and_writes_nothing(monkeypatch, capsys, pr_state, said):
    repo = _Repo(monkeypatch, _merged_state(), {123: pr_state})
    body = repo.issue["body"]
    cli.main(["reconcile", "--dry-run"])
    assert (repo.issue["body"], repo.notes, repo.blocks) == (body, [], [])
    merged, abandoned = int(pr_state == "MERGED"), int(pr_state == "CLOSED")
    assert capsys.readouterr().out == said + (
        f"Scanned 1 feature issue(s): would close out {merged} merged, would close {abandoned} abandoned, "
        "0 needing a human decision.\n"
    )


def test_sync_refuses_a_stale_schema_before_it_syncs(monkeypatch):
    synced: list[bool] = []
    _stub_issue(monkeypatch, _merged_state() | {"schema": 1})
    monkeypatch.setattr(cli, "git_town_sync", lambda stack: synced.append(stack))
    with pytest.raises(StaleSchema):
        cli.main(["sync", "--branch", "add-oauth"])
    assert synced == []


@pytest.fixture
def lock_waited(monkeypatch) -> threading.Event:
    waited = threading.Event()

    def flock(fd, operation):
        try:
            return fcntl.flock(fd, operation)
        except BlockingIOError:
            waited.set()
            raise

    monkeypatch.setattr(feature, "fcntl", SimpleNamespace(flock=flock, LOCK_EX=fcntl.LOCK_EX, LOCK_NB=fcntl.LOCK_NB))
    return waited


def _waits_for_the_lock(lock_waited: threading.Event, branch: str, command, written: list) -> None:
    with ThreadPoolExecutor(1) as pool:
        with feature.locked(branch):
            run = pool.submit(command)
            assert lock_waited.wait(timeout=10), "the command didn't wait for the feature's lock"
            assert written == []
        run.result()
    assert written


def test_adopt_holds_the_feature_from_its_tracking_check_to_its_last_write(monkeypatch, lock_waited):
    did = _stub_adopt(monkeypatch, parent=None, pr=None, pr_base=None)
    written = []
    open_issue = cli._open_feature_issue
    monkeypatch.setattr(cli, "_open_feature_issue", lambda *a: written.append("issue") or open_issue(*a))
    _waits_for_the_lock(lock_waited, "child", lambda: cli.cmd_adopt(_adopt_args()), written)
    assert did["issue"] == "main"


def test_problem_add_holds_the_feature_while_it_files(monkeypatch, lock_waited):
    monkeypatch.setattr(feature, "resolve_issue", lambda branch: 7)
    calls = _stub_filing(monkeypatch, [])
    args = cli.argparse.Namespace(branch="add-oauth", title="t", sev="low", body="d", body_file=None, repeats=None)
    _waits_for_the_lock(lock_waited, "add-oauth", lambda: cli.cmd_problem_add(args), calls)


def test_hand_off_holds_the_feature_while_it_hands_the_problem_off(monkeypatch, lock_waited):
    did = _stub_problem_edits(monkeypatch, _problem(8, "sev:low"))
    _stub_pr(monkeypatch, "MERGED")
    args = cli.argparse.Namespace(branch="add-oauth", number=8, reason="fix in a separate PR")
    _waits_for_the_lock(lock_waited, "add-oauth", lambda: cli.cmd_problem_hand_off(args), did)


def test_a_long_non_ascii_branch_takes_its_lock():
    branch = "feature/" + "исправить-гонку-при-записи-состояния-фичи-из-двух-сессий"
    with feature.locked(branch):
        pass
