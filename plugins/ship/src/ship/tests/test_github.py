"""Tests for the GitHub layer helpers that have branching logic worth pinning."""

import pytest

from ship import github


def test_open_pr_for_branch_returns_number_when_present(monkeypatch):
    monkeypatch.setattr(github, "run", lambda cmd: "1910")
    assert github.open_pr_for_branch("repl-persistent-kernel") == 1910


def test_open_pr_for_branch_returns_none_when_no_pr(monkeypatch):
    # `gh pr list ... --jq '.[0].number // empty'` prints nothing when no PR matches;
    # that empty result is a real "no PR", not a swallowed error.
    monkeypatch.setattr(github, "run", lambda cmd: "")
    assert github.open_pr_for_branch("some-branch") is None


def _fake_pr_body_editor(monkeypatch, body: str) -> dict:
    """Stub github.run so `gh pr view` returns `body` and `gh pr edit` captures the new body."""
    captured: dict = {}

    def fake_run(cmd, *, input_text=None):
        if cmd[:3] == ["gh", "pr", "view"]:
            return body
        if cmd[:3] == ["gh", "pr", "edit"]:
            captured["body"] = input_text
            return ""
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(github, "run", fake_run)
    return captured


QUOTE = "> Feature tracking issue: closes #1964"
BLOCK = f"<!-- ship -->\n{QUOTE}\n<!-- /ship -->"


def test_set_pr_block_adds_the_block(monkeypatch):
    captured = _fake_pr_body_editor(monkeypatch, "Some PR description.")
    assert github.set_pr_block(1910, QUOTE, 1964) is True
    assert captured["body"] == f"Some PR description.\n\n{BLOCK}\n"


def test_set_pr_block_skips_the_edit_when_nothing_changes(monkeypatch):
    captured = _fake_pr_body_editor(monkeypatch, f"Desc.\n\n{BLOCK}\n")
    assert github.set_pr_block(1910, QUOTE, 1964) is False
    assert "body" not in captured


def test_set_pr_block_replaces_only_the_block(monkeypatch):
    # The PR body's own `Closes #<problem>` lines must survive, or re-running `feature pr` would
    # silently stop those problem sub-issues from auto-closing.
    old = "Desc.\n\nCloses #2173\n\n<!-- ship -->\n> Feature tracking issue: closes #1981\n<!-- /ship -->\n"
    captured = _fake_pr_body_editor(monkeypatch, old)
    assert github.set_pr_block(1910, QUOTE, 1964) is True
    assert captured["body"] == f"Desc.\n\nCloses #2173\n\n{BLOCK}\n"


def test_set_pr_block_drops_the_old_reference_lines(monkeypatch):
    # PRs linked before the block held the reference carry a line of their own: the marked
    # `Closes` under the current or the pre-rename marker, or the non-closing `Part of #<issue>`.
    for line in ("Closes #1964 <!-- ship -->", "Closes #1964 <!-- feature-workflow -->", "Part of #1964"):
        captured = _fake_pr_body_editor(monkeypatch, f"Desc.\n\n{line}\n")
        assert github.set_pr_block(1910, QUOTE, 1964) is True
        assert captured["body"] == f"Desc.\n\n{BLOCK}\n"


def test_pr_states_batches_into_one_aliased_query(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["gh", "repo", "view"]:
            return "acme\tnavari"
        return '{"p2494": {"number": 2494, "state": "MERGED"}, "p2506": {"number": 2506, "state": "OPEN"}}'

    monkeypatch.setattr(github, "run", fake_run)
    assert github.pr_states([2506, 2494, 2506]) == {2494: "MERGED", 2506: "OPEN"}
    # One repo lookup + ONE graphql call for both PRs (a `gh pr view` each is what made the sweep
    # too slow to run from a hook).
    assert len(calls) == 2
    query = next(arg for arg in calls[1] if arg.startswith("query="))
    assert "p2494: pullRequest(number:2494)" in query
    assert "p2506: pullRequest(number:2506)" in query


def test_pr_states_asks_nothing_when_given_nothing(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise AssertionError(f"should not have run: {cmd}")

    monkeypatch.setattr(github, "run", fake_run)
    assert github.pr_states([]) == {}


def test_feature_issues_flattens_paginated_pages(monkeypatch):
    # `gh api graphql --paginate --jq` prints one JSON array per page; every page must land in the
    # result, or the oldest features stay invisible to the reconcile sweep forever.
    page1 = '[{"number": 1, "title": "A", "state": "CLOSED", "body": "x"}]'
    page2 = '[{"number": 2, "title": "B", "state": "OPEN", "body": "y"}]'

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "repo", "view"]:
            return "acme\tnavari"
        assert "--paginate" in cmd
        return f"{page1}\n{page2}"

    monkeypatch.setattr(github, "run", fake_run)
    assert [i["number"] for i in github.feature_issues()] == [1, 2]


def test_pr_changed_paths_pages_the_files_listing(monkeypatch):
    # `gh pr diff` 406s on any PR over 300 files; the REST listing must be paginated instead.
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "repo", "view"]:
            return "acme\tnavari"
        assert cmd[:3] == ["gh", "api", "repos/acme/navari/pulls/7/files?per_page=100"]
        assert "--paginate" in cmd
        return "a.py\nb/c.py\nd.md"

    monkeypatch.setattr(github, "run", fake_run)
    assert github.pr_changed_paths(7, expected=3) == ["a.py", "b/c.py", "d.md"]


def test_pr_changed_paths_refuses_a_truncated_listing(monkeypatch):
    # GitHub stops the listing at 3000 files without an error; a short list must crash, not
    # quietly size the budget on the paths it happened to return.
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "repo", "view"]:
            return "acme\tnavari"
        return "a.py\nb.py"

    monkeypatch.setattr(github, "run", fake_run)
    with pytest.raises(RuntimeError, match="3 changed files but the files listing returned 2"):
        github.pr_changed_paths(7, expected=3)


def test_remote_owner_parses_https_and_ssh():
    assert github.remote_owner("https://github.com/vladsavelyev/skills.git") == "vladsavelyev"
    assert github.remote_owner("git@github.com:AstraZeneca/navari.git") == "AstraZeneca"
    assert github.remote_owner("https://gitlab.com/x/y.git") is None


def test_is_work_repo_by_active_account_or_its_orgs():
    assert github.is_work_repo("kbww511_AZU", "kbww511_azu", [])
    assert github.is_work_repo("azu-oncology-rd", "kbww511_azu", ["azu-oncology-rd", "GHCP-Business"])
    assert not github.is_work_repo("vladsavelyev", "kbww511_azu", ["azu-oncology-rd"])
    assert not github.is_work_repo("t3dotgg", "kbww511_azu", ["azu-oncology-rd"])


def test_personal_account_is_the_one_other_logged_in_account():
    accounts = [
        {"login": "kbww511_azu", "active": True, "state": "success"},
        {"login": "vladsavelyev", "active": False, "state": "success"},
        {"login": "stale", "active": False, "state": "error"},
    ]
    assert github.personal_account(accounts) == "vladsavelyev"


@pytest.mark.parametrize("others", [[], ["a", "b"]])
def test_personal_account_aborts_unless_exactly_one(others):
    accounts = [{"login": "work", "active": True, "state": "success"}] + [
        {"login": o, "active": False, "state": "success"} for o in others
    ]
    with pytest.raises(SystemExit):
        github.personal_account(accounts)
