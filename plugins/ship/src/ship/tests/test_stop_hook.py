"""The Stop hook: a held session goes on until its gate settles."""

import io
import json

from ship import github, hold, stop_hook
from ship.errors import Refusal
from ship.gate import GateDecision, Verdict


def _run(monkeypatch, capsys, tmp_path, verdict: Verdict, *, checkout: str | None = None) -> str:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    hold.hold("s1", branch="fix-it", checkout=checkout or str(tmp_path))
    decision = GateDecision(
        verdict=verdict, reasons=["1 problem(s) open, every one blocking"], next_step="Fix it.", rounds_used=1, budget=2
    )
    blocking = [{"number": 5400, "title": "stale cache", "state": "OPEN", "labels": ["sev:med"]}]
    monkeypatch.setattr(stop_hook.os, "chdir", lambda path: None)
    monkeypatch.setattr(github, "use_owner_account", lambda: None)
    monkeypatch.setattr(stop_hook.feature, "gate_for", lambda branch: (39, {"pr": 5355}, blocking, decision))
    monkeypatch.setattr(stop_hook, "feature_issue", lambda branch: 39)
    monkeypatch.setattr(stop_hook, "worktree_for_branch", lambda branch: "/repo/.claude/worktrees/fix-it")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "s1", "stop_hook_active": False})))
    stop_hook.main()
    return capsys.readouterr().out


def test_a_held_session_with_a_problem_open_is_sent_back_to_fix_it(monkeypatch, capsys, tmp_path):
    """navari #5355: the round filed #5400 and the run ended. Now ending the turn there is refused."""
    out = json.loads(_run(monkeypatch, capsys, tmp_path, Verdict.REVIEW_AGAIN))
    assert out["decision"] == "block"
    assert "#5400 [sev:med] stale cache" in out["reason"] and "feature problem resolve" in out["reason"]
    assert hold.held("s1") != []


def test_a_settled_gate_lets_the_session_stop_and_drops_the_hold(monkeypatch, capsys, tmp_path):
    assert _run(monkeypatch, capsys, tmp_path, Verdict.OPEN) == ""
    assert hold.held("s1") == []


def test_a_spent_budget_still_holds_until_it_is_escalated(monkeypatch, capsys, tmp_path):
    out = json.loads(_run(monkeypatch, capsys, tmp_path, Verdict.NEEDS_DECISION))
    assert out["decision"] == "block" and "feature escalate" in out["reason"]


def test_a_hold_whose_checkout_is_gone_is_dropped_out_loud(monkeypatch, capsys, tmp_path):
    out = json.loads(_run(monkeypatch, capsys, tmp_path, Verdict.REVIEW_AGAIN, checkout=str(tmp_path / "gone")))
    assert "decision" not in out and "is gone" in out["systemMessage"]
    assert hold.held("s1") == []


def test_a_session_holding_nothing_stops(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(
        stop_hook.feature, "gate_for", lambda branch: (_ for _ in ()).throw(AssertionError("judged a gate"))
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": "s2"})))
    stop_hook.main()
    assert capsys.readouterr().out == ""


def _two_holds(monkeypatch, tmp_path, *, feat_error: Exception | None, stop_hook_active: bool = False):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    hold.hold("s1", branch="feat", checkout=str(tmp_path))
    hold.hold("s1", branch="fix-it", checkout=str(tmp_path))
    decision = GateDecision(verdict=Verdict.REVIEW_AGAIN, reasons=[], next_step="", rounds_used=1, budget=2)

    def gate_for(branch):
        if branch == "feat" and feat_error:
            raise feat_error
        return 39, {"pr": 5355}, [], decision

    monkeypatch.setattr(stop_hook.os, "chdir", lambda path: None)
    monkeypatch.setattr(github, "use_owner_account", lambda: None)
    monkeypatch.setattr(stop_hook, "feature_issue", lambda branch: None if branch == "untracked" else 39)
    monkeypatch.setattr(stop_hook.feature, "gate_for", gate_for)
    monkeypatch.setattr(stop_hook, "worktree_for_branch", lambda branch: None)
    payload = {"session_id": "s1", "stop_hook_active": stop_hook_active}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))


def test_a_hold_whose_branch_isnt_tracked_is_dropped_and_the_others_still_hold(monkeypatch, capsys, tmp_path):
    """#137: a stray hold on an untracked branch ended the hook before it checked the real one."""
    _two_holds(monkeypatch, tmp_path, feat_error=None)
    hold.hold("s1", branch="untracked", checkout=str(tmp_path))
    stop_hook.main()
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and "`fix-it`" in out["reason"]
    assert "`untracked`: it isn't a tracked feature" in out["systemMessage"]
    assert [h["branch"] for h in hold.held("s1")] == ["feat", "fix-it"]


def test_a_gate_github_cant_answer_keeps_the_hold_and_holds_the_stop_once(monkeypatch, capsys, tmp_path):
    """#147: a 503 used to drop the hold, ending the run with its problems open."""
    _two_holds(monkeypatch, tmp_path, feat_error=Refusal("gh: HTTP 503"))
    stop_hook.main()
    out = json.loads(capsys.readouterr().out)
    assert out["decision"] == "block" and "HTTP 503" in out["reason"]
    assert [h["branch"] for h in hold.held("s1")] == ["feat", "fix-it"]


def test_a_second_stop_in_a_row_with_github_down_goes_through_with_the_error_shown(monkeypatch, capsys, tmp_path):
    _two_holds(monkeypatch, tmp_path, feat_error=Refusal("gh: HTTP 503"), stop_hook_active=True)
    hold.release("s1", branch="fix-it", checkout=str(tmp_path))
    stop_hook.main()
    out = json.loads(capsys.readouterr().out)
    assert "decision" not in out and "HTTP 503" in out["systemMessage"]
    assert [h["branch"] for h in hold.held("s1")] == ["feat"]
