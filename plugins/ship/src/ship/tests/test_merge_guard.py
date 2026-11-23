"""The merge guard: which commands are merges, and what it lets through."""

import io
import json

import pytest

from ship import github, merge_guard
from ship.errors import Refusal
from ship.gate import GateDecision, Verdict
from ship.merge_guard import MergeCall, merge_calls


def test_a_bare_merge_names_the_current_branchs_pr():
    assert merge_calls("gh pr merge --admin --squash", "/repo") == [
        MergeCall(target=None, repo=None, cwd="/repo", override=None)
    ]


def test_flag_values_are_not_read_as_the_pr():
    [call] = merge_calls('gh pr merge -t "Ship 12" --body=x -R org/app 5355 -d', "/repo")
    assert (call.target, call.repo) == ("5355", "org/app")


def test_cd_and_the_override_carry_into_the_merge_and_other_commands_dont_count():
    command = 'cd sub && git push\nSHIP_PAST_GATE="user: ship it" /usr/bin/gh pr merge 7; gh pr view 7'
    assert merge_calls(command, "/repo") == [
        MergeCall(target="7", repo=None, cwd="/repo/sub", override="user: ship it")
    ]


@pytest.mark.parametrize("command", ["gh pr view 7", "echo 'gh pr merge 7'", "git merge origin/main", "gh pr merged"])
def test_commands_that_only_mention_a_merge_are_not_merges(command):
    assert merge_calls(command, "/repo") == []


def _stub_pr(monkeypatch, *, verdict: Verdict, body: str = "> **Tracking issue:** closes #39") -> list[tuple[int, str]]:
    posted: list[tuple[int, str]] = []
    pr = {
        "number": 5355,
        "repo": "org/app",
        "state": "OPEN",
        "headRefName": "fix-it",
        "headRefOid": "f" * 40,
        "body": f"Why.\n\n<!-- ship -->\n{body}\n<!-- /ship -->\n",
    }
    decision = GateDecision(
        verdict=verdict, reasons=["1 problem(s) open, every one blocking"], next_step="", rounds_used=1, budget=2
    )
    monkeypatch.setattr(merge_guard.os, "chdir", lambda path: None)
    monkeypatch.setattr(github, "use_owner_account", lambda: None)
    monkeypatch.setattr(github, "pr_view", lambda target, repo: pr)
    monkeypatch.setattr(github, "current_repo", lambda: "org/app")
    monkeypatch.setattr(merge_guard, "has_object", lambda sha: True)
    monkeypatch.setattr(github, "get_issue_body", lambda n: "")
    monkeypatch.setattr(merge_guard, "parse_block", lambda body: {"pr": 5355})
    monkeypatch.setattr(
        github,
        "sub_issues",
        lambda n: [{"number": 5400, "title": "stale cache", "state": "OPEN", "labels": ["sev:med"]}],
    )
    monkeypatch.setattr(merge_guard.feature, "decide", lambda branch, state, subs, merged, head: decision)
    monkeypatch.setattr(github, "comment", lambda n, text: posted.append((n, text)))
    return posted


def _call(override=None):
    return MergeCall(target="5355", repo=None, cwd="/repo", override=override)


def test_the_navari_5355_admin_merge_is_refused_with_whats_open(monkeypatch):
    posted = _stub_pr(monkeypatch, verdict=Verdict.REVIEW_AGAIN)
    refusal = merge_guard.check(_call())
    assert "refuses to merge PR #5355" in refusal and "#5400 [sev:med] stale cache" in refusal
    assert posted == []


def test_the_users_reason_lets_it_through_on_the_record(monkeypatch):
    posted = _stub_pr(monkeypatch, verdict=Verdict.REVIEW_AGAIN)
    assert merge_guard.check(_call(override="demo at 3pm, fix after")) is None
    [(issue, text)] = posted
    assert issue == 39 and "demo at 3pm, fix after" in text and "#5400 [sev:med]" in text


def test_an_open_gate_merges(monkeypatch):
    posted = _stub_pr(monkeypatch, verdict=Verdict.OPEN)
    assert merge_guard.check(_call()) is None
    assert posted == []


def test_a_pr_ship_doesnt_track_merges(monkeypatch):
    _stub_pr(monkeypatch, verdict=Verdict.REVIEW_AGAIN, body="> nothing of ship's")
    assert merge_guard.check(_call()) is None


def test_a_tracked_pr_of_another_repo_than_the_checkout_is_refused(monkeypatch):
    _stub_pr(monkeypatch, verdict=Verdict.OPEN)
    monkeypatch.setattr(github, "current_repo", lambda: "org/other")
    assert "run the merge from a checkout of org/app" in merge_guard.check(_call())


def _run_hook(monkeypatch, capsys, command: str) -> str:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": "/repo"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    merge_guard.main()
    return capsys.readouterr().out


def test_an_error_checking_a_merge_denies_it(monkeypatch, capsys):
    def boom(call):
        raise RuntimeError("gh: HTTP 503")

    monkeypatch.setattr(merge_guard, "check", boom)
    out = json.loads(_run_hook(monkeypatch, capsys, "gh pr merge 5355 --admin"))["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "HTTP 503" in out["permissionDecisionReason"]


def test_other_commands_pass_without_a_word(monkeypatch, capsys):
    monkeypatch.setattr(merge_guard, "check", lambda call: pytest.fail("checked a non-merge"))
    assert _run_hook(monkeypatch, capsys, "git merge origin/main") == ""


def test_a_refusal_from_the_account_check_denies_the_merge(monkeypatch, capsys):
    """#136: `personal_account` used to end the process with SystemExit, which `except Exception`
    let through, so the hook exited quietly and the merge ran unchecked."""

    def two_personal_accounts():
        raise Refusal("Found 2 personal accounts")

    monkeypatch.setattr(merge_guard.os, "chdir", lambda path: None)
    monkeypatch.setattr(github, "use_owner_account", two_personal_accounts)
    out = json.loads(_run_hook(monkeypatch, capsys, "gh pr merge 5355 --admin"))["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "Found 2 personal accounts" in out["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        "command gh pr merge 5 --admin",
        "if true; then gh pr merge 5 --admin; fi",
        "env A=1 gh pr merge 5",
        "sudo -u me gh pr merge 5",
        "x && { gh pr merge 5; }",
        "out=$(gh pr merge 5)",
        "echo `gh pr merge 5`",
        "cat <<EOF\n$(gh pr merge 5)\nEOF",
    ],
)
def test_wrapped_and_nested_merges_are_merges(command):
    """#138: each of these ran the merge and the guard found none."""
    assert [c.target for c in merge_calls(command, "/repo")] == ["5"]


def test_a_heredoc_body_is_data_even_with_an_apostrophe():
    command = "gh pr merge 5 --admin && cat <<'EOF'\ndon't gh pr merge 6\nEOF\necho done"
    assert [c.target for c in merge_calls(command, "/repo")] == ["5"]


def test_the_override_survives_a_token_from_a_substitution():
    """The skills tell agents to prefix GH_TOKEN=$(gh auth token --user …) for a personal repo."""
    [call] = merge_calls('SHIP_PAST_GATE="ok" GH_TOKEN=$(gh auth token --user v) gh pr merge 5', "/repo")
    assert (call.target, call.override) == ("5", "ok")


def test_an_unreadable_command_that_mentions_a_merge_is_refused(monkeypatch, capsys):
    out = json.loads(_run_hook(monkeypatch, capsys, "gh pr merge 5 --admin; echo 'unclosed"))["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and "can't read this command" in out["permissionDecisionReason"]


def test_an_unreadable_command_that_merges_nothing_passes(monkeypatch, capsys):
    assert _run_hook(monkeypatch, capsys, "git merge x; echo 'unclosed") == ""


def test_a_part_of_line_naming_a_feature_issue_is_tracked(monkeypatch):
    _stub_pr(monkeypatch, verdict=Verdict.REVIEW_AGAIN)
    pr = github.pr_view(None, repo=None) | {"body": "Why.\n\nPart of #12\nPart of #39\n"}
    monkeypatch.setattr(github, "pr_view", lambda target, repo: pr)
    monkeypatch.setattr(github, "issue_labels", lambda n: ["feature"] if n == 39 else ["bug"])
    assert "feature #39" in merge_guard.check(_call())


@pytest.mark.parametrize(
    ("command", "repo"),
    [
        ("gh pr --repo org/app merge 5", "org/app"),
        ("gh pr -R org/app merge 5 --admin", "org/app"),
        ("bash -lc 'gh pr merge 5'", None),
        ('sh -c "cd x && gh pr merge 5"', None),
        ("timeout 60 gh pr merge 5", None),
        ("echo 5 | xargs gh pr merge", None),
    ],
)
def test_round_2s_forms_are_merges(command, repo):
    """#138, rediscovered: a --repo before `merge`, a shell's -c script, a wrapper with arguments."""
    [call] = merge_calls(command, "/repo")
    assert call.repo == repo


def test_a_single_quoted_commit_message_mentioning_a_merge_is_not_one():
    """#145: substitutions were lifted out of single quotes, so this commit was refused."""
    assert merge_calls("git commit -m 'refuse `gh pr merge` $(while shut)'", "/repo") == []


def test_a_subshells_cd_stays_in_the_subshell():
    """#146: the `cd` inside the parentheses sent the outer merge to the wrong repo."""
    calls = merge_calls("(cd ../other && gh pr merge 1); gh pr merge 2", "/repo")
    assert [(c.target, c.cwd) for c in calls] == [("1", "/other"), ("2", "/repo")]


def test_eval_runs_in_this_shell_so_its_cd_carries():
    assert [c.cwd for c in merge_calls('eval "cd sub"; gh pr merge 2', "/repo")] == ["/repo/sub"]
