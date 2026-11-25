"""The merge guard: which commands are merges, and what it lets through."""

import io
import json
import re
from pathlib import Path

import pytest

from ship import github, merge_guard
from ship.errors import Refusal
from ship.gate import GateDecision, Verdict
from ship.merge_guard import MergeCall, merge_calls


def test_a_bare_merge_names_the_current_branchs_pr():
    assert merge_calls("gh pr merge --admin --squash", "/repo") == [
        MergeCall(target=None, repo=None, cwd="/repo", override=None, admin=True)
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


def _stub_pr(
    monkeypatch, *, verdict: Verdict, body: str = "> **Tracking issue:** closes #39", queue: bool = False
) -> list[tuple[int, str]]:
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
    monkeypatch.setattr(github, "merge_queue_enabled", lambda number, repo: queue)
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


def _run_t3_hook(monkeypatch, capsys, tool_input: dict) -> str:
    payload = {"tool_name": merge_guard.T3_MERGE, "tool_input": tool_input, "cwd": "/repo"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    merge_guard.main()
    return capsys.readouterr().out


def test_t3s_merge_tool_is_refused_at_a_shut_gate(monkeypatch, capsys):
    """#179: T3 tells agents to merge with `pull_request_merge`, which never reached the guard."""
    _stub_pr(monkeypatch, verdict=Verdict.REVIEW_AGAIN)
    tool_input = {"number": 5355, "repository": "org/app", "expectedHeadSha": "f" * 40}
    out = json.loads(_run_t3_hook(monkeypatch, capsys, tool_input))
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "refuses to merge PR #5355" in reason and "#5400 [sev:med] stale cache" in reason


@pytest.mark.parametrize(
    ("tool_input", "command"),
    [
        ({"method": "squash", "expectedHeadSha": "a" * 40}, "--squash --match-head-commit " + "a" * 40),
        ({}, "--merge --match-head-commit " + "f" * 40),
        ({"method": "rebase", "auto": True}, "--rebase --auto --match-head-commit " + "f" * 40),
    ],
)
def test_a_refused_t3_merge_names_the_gh_merge_that_makes_it(monkeypatch, capsys, tool_input, command):
    _stub_pr(monkeypatch, verdict=Verdict.REVIEW_AGAIN)
    out = json.loads(_run_t3_hook(monkeypatch, capsys, {"number": 5355, "repository": "org/app"} | tool_input))
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert f'`SHIP_PAST_GATE="<their reason>" gh pr merge 5355 --repo org/app {command}`;' in reason


def test_a_refused_t3_fast_forward_leaves_the_method_to_the_user(monkeypatch, capsys):
    _stub_pr(monkeypatch, verdict=Verdict.REVIEW_AGAIN)
    tool_input = {"number": 5355, "repository": "org/app", "method": "fast-forward"}
    reason = json.loads(_run_t3_hook(monkeypatch, capsys, tool_input))["hookSpecificOutput"]["permissionDecisionReason"]
    assert "gh pr merge 5355 --repo org/app --merge|--squash|--rebase --match-head-commit" in reason
    assert "with the method the user picks: GitHub has no fast-forward merge" in reason


def test_a_refused_gh_merge_is_rerun_with_the_reason_in_front(monkeypatch, capsys):
    _stub_pr(monkeypatch, verdict=Verdict.REVIEW_AGAIN)
    out = json.loads(_run_hook(monkeypatch, capsys, "gh pr merge 5355 --squash"))["hookSpecificOutput"]
    assert (
        're-run the command with `SHIP_PAST_GATE="<their reason>"` in front of `gh`'
        in (out["permissionDecisionReason"])
    )


def test_t3s_merge_tool_merges_at_an_open_gate(monkeypatch, capsys):
    _stub_pr(monkeypatch, verdict=Verdict.OPEN)
    assert (
        _run_t3_hook(monkeypatch, capsys, {"number": 5355, "repository": "org/app", "expectedHeadSha": "f" * 40}) == ""
    )


def test_t3s_merge_input_names_the_pr():
    call = merge_guard.t3_merge_call({"number": 7, "repository": "org/app", "auto": True}, "/repo")
    assert call == MergeCall(target="7", repo="org/app", cwd="/repo", override=None, auto=True, method="merge")


@pytest.mark.parametrize("tool_input", [{"expectedHeadSha": "abc"}, {"number": 7}, {"environmentId": "laptop"}])
def test_a_t3_merge_must_name_the_pr_and_its_repo(monkeypatch, capsys, tool_input):
    """#193: T3's default is its thread's branch, not the branch of the cwd the hook sees."""
    monkeypatch.setattr(merge_guard, "check", lambda call: pytest.fail("guessed T3's PR"))
    out = json.loads(_run_t3_hook(monkeypatch, capsys, tool_input))
    assert "name the PR with `number` and `repository`" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_auto_merge_of_a_tracked_pr_is_refused_even_at_an_open_gate(monkeypatch):
    posted = _stub_pr(monkeypatch, verdict=Verdict.OPEN)
    refusal = merge_guard.check(MergeCall(target="5355", repo=None, cwd="/repo", override=None, auto=True))
    assert "refuses to merge PR #5355 with auto-merge" in refusal and posted == []


def test_the_users_reason_lets_an_auto_merge_through_on_the_record(monkeypatch):
    posted = _stub_pr(monkeypatch, verdict=Verdict.OPEN)
    assert merge_guard.check(MergeCall(target="5355", repo=None, cwd="/repo", override="ok", auto=True)) is None
    [(issue, text)] = posted
    assert issue == 39 and "PR #5355 with auto-merge past the gate" in text


@pytest.mark.parametrize(
    ("command", "auto"),
    [
        ("gh pr merge 5 --auto --squash", True),
        ("gh pr merge 5 --auto=true --squash", True),
        ("gh pr merge 5 --auto=false", False),
        ('gh pr merge 5 --squash --body "--auto"', False),
        ('gh pr merge 5 -b"--auto"', False),
        ("gh pr merge 5 --disable-auto=false", False),
    ],
)
def test_gh_auto_is_read_from_the_flag_not_the_words(command, auto):
    """#189: `"--auto" in args` also matched a body, and missed `--auto=true`."""
    [call] = merge_calls(command, "/repo")
    assert call.auto is auto


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 5 --disable-auto",
        "gh pr merge 5 --disable-auto=true",
        "gh pr merge --help",
        "gh pr merge 5 -h",
        "gh pr merge -dh 5",
    ],
)
def test_disabling_auto_merge_or_asking_for_help_is_no_merge(command):
    assert merge_calls(command, "/repo") == []


def test_a_body_naming_disable_auto_is_still_a_merge():
    """#189: this merged with no gate check at all."""
    assert [c.target for c in merge_calls('gh pr merge 5355 --merge --body "--disable-auto"', "/repo")] == ["5355"]


def test_a_short_flag_cluster_takes_its_value_from_the_next_word():
    [call] = merge_calls("gh pr merge -dR org/app 5 -st subject", "/repo")
    assert (call.target, call.repo) == ("5", "org/app")


@pytest.mark.parametrize(
    ("command", "flag"),
    [
        ("gh pr merge 5 --repo", "--repo"),
        ("gh pr merge 5 -dR", "-dR"),
        ("gh pr merge 5 --match-head-commit", "--match-head-commit"),
        ("gh pr merge 5 --auto=yes", "--auto=yes"),
        ("gh pr merge 5 -d=no", "-d=no"),
    ],
)
def test_a_merge_flag_gh_would_refuse_is_refused_not_misread(monkeypatch, capsys, command, flag):
    monkeypatch.setattr(merge_guard, "check", lambda call: pytest.fail("misread a flag gh refuses"))
    out = json.loads(_run_hook(monkeypatch, capsys, command))["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny" and f"gh would refuse `{flag}`" in out["permissionDecisionReason"]


def test_flags_of_other_gh_commands_are_not_judged():
    command = "gh pr view 5 --json=title && gh pr merge 5 --auto=F -d=1"
    assert merge_calls(command, "/repo") == [MergeCall(target="5", repo=None, cwd="/repo", override=None)]


def test_a_t3_stack_merge_is_refused(monkeypatch, capsys):
    """#190, #192: T3 reads the lower layers' heads after the hook, so none of them can be judged."""
    monkeypatch.setattr(merge_guard, "check", lambda call: pytest.fail("judged a stack merge"))
    out = json.loads(_run_t3_hook(monkeypatch, capsys, {"number": 9, "repository": "org/app", "stack": True}))
    assert "merge the PRs one at a time" in out["hookSpecificOutput"]["permissionDecisionReason"]


def _judged_head(monkeypatch, run) -> str:
    _stub_pr(monkeypatch, verdict=Verdict.OPEN)
    heads = []
    decision = GateDecision(verdict=Verdict.OPEN, reasons=[], next_step="", rounds_used=1, budget=2)
    monkeypatch.setattr(merge_guard.feature, "decide", lambda *args, head: heads.append(head) or decision)
    run()
    [head] = heads
    return head


def test_the_gate_is_judged_at_the_head_the_merge_pins(monkeypatch, capsys):
    """#192: the guard read the PR's head itself, not the commit T3 or gh would land."""
    t3 = {"number": 5355, "repository": "org/app", "expectedHeadSha": "a" * 40}
    assert _judged_head(monkeypatch, lambda: _run_t3_hook(monkeypatch, capsys, t3)) == "a" * 40
    gh = "gh pr merge 5355 --match-head-commit " + "b" * 40
    assert _judged_head(monkeypatch, lambda: _run_hook(monkeypatch, capsys, gh)) == "b" * 40
    assert _judged_head(monkeypatch, lambda: _run_hook(monkeypatch, capsys, "gh pr merge 5355")) == "f" * 40


def test_a_merge_into_a_queue_is_refused_even_at_an_open_gate(monkeypatch, capsys):
    """#191: T3 and gh join a merge queue by auto-merge, which lands a head pushed after the check."""
    posted = _stub_pr(monkeypatch, verdict=Verdict.OPEN, queue=True)
    refusal = "refuses to merge PR #5355 through its base's merge queue"
    assert refusal in merge_guard.check(_call())
    t3 = {"number": 5355, "repository": "org/app", "expectedHeadSha": "f" * 40}
    assert (
        refusal in json.loads(_run_t3_hook(monkeypatch, capsys, t3))["hookSpecificOutput"]["permissionDecisionReason"]
    )
    assert posted == []


def test_an_admin_merge_skips_the_queue_so_merges_at_an_open_gate(monkeypatch):
    _stub_pr(monkeypatch, verdict=Verdict.OPEN, queue=True)
    [call] = merge_calls("gh pr merge 5355 --admin", "/repo")
    assert merge_guard.check(call) is None


def test_the_hook_runs_on_bash_and_t3s_merge_tool():
    """#194: a rename of the tool on either side would stop the hook firing for T3 merges."""
    hooks = json.loads((Path(merge_guard.__file__).parents[2] / "hooks" / "hooks.json").read_text())["hooks"]
    [matcher] = [h["matcher"] for h in hooks["PreToolUse"] if "feature-merge-guard" in h["hooks"][0]["command"]]
    assert re.fullmatch(matcher, "Bash") and re.fullmatch(matcher, merge_guard.T3_MERGE)
