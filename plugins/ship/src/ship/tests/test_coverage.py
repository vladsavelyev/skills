"""The commit walk behind the gate's coverage rule, on real git repositories: which commits since
the last review still need one."""

import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

from ship import __main__ as cli
from ship import feature, github
from ship.shell import CommandFailed
from ship.state import new_state, record_fix, record_review

LOW, HIGH = 5, 6


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(path: str, text: str, message: str) -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(text)
    git("add", path)
    git("commit", "-q", "-m", message)
    return git("rev-parse", "HEAD")


def merge(ref: str) -> str:
    git("merge", "-q", "--no-edit", "--no-ff", ref)
    return git("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A clone of a bare origin, on branch `feat` off `main`, its PR #9 reviewed at its tip."""
    git("init", "-q", "--bare", "-b", "main", str(tmp_path / "origin.git"))
    git("clone", "-q", str(tmp_path / "origin.git"), str(tmp_path / "work"))
    monkeypatch.chdir(tmp_path / "work")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    git("checkout", "-q", "-b", "main")
    commit("shared.py", "a\n", "base")
    git("push", "-q", "origin", "main")
    git("checkout", "-q", "-b", "feat")
    reviewed = commit("feat.py", "feature\n", "feature")
    monkeypatch.setattr(github, "pr_base_branch", lambda pr: "main")
    monkeypatch.setattr(feature, "sensitive_patterns", lambda: [])
    state = new_state(branch="feat", base="main", updated="t0")
    state["pr"] = 9
    state["last_review"] = {"run": 1, "sha": reviewed[:9]}
    return state


SUBS = [
    {"number": LOW, "title": "a docstring", "state": "CLOSED", "labels": ["problem", "sev:low"]},
    {"number": HIGH, "title": "a crash", "state": "CLOSED", "labels": ["problem", "sev:high"]},
]


def advance_main(path: str, text: str) -> None:
    git("checkout", "-q", "main")
    commit(path, text, f"main changes {path}")
    git("push", "-q", "origin", "main")
    git("checkout", "-q", "feat")


def unreviewed(state: dict) -> list[str]:
    return feature.unreviewed(state, git("rev-parse", "HEAD"), SUBS)


def test_fixes_of_low_problems_and_a_clean_base_merge_need_no_review(repo):
    """A low fix, a merge of the base that git made by itself, another low fix."""
    record_fix(repo, LOW, [commit("feat.py", "feature, documented\n", "fix the docstring")])
    advance_main("other.py", "x\n")
    merge("origin/main")
    record_fix(repo, LOW, [commit("feat.py", "feature, documented well\n", "fix the docstring again")])
    assert unreviewed(repo) == []


def test_new_work_and_fixes_of_serious_problems_need_review(repo):
    work = commit("more.py", "more\n", "more feature")
    high_fix = commit("feat.py", "feature, fixed\n", "fix the crash")
    both = commit("feat.py", "feature, fixed and documented\n", "fix the crash and the docstring")
    record_fix(repo, HIGH, [high_fix, both])
    record_fix(repo, LOW, [both])
    assert unreviewed(repo) == [work, high_fix, both]


def continue_after_resolving(path: str, text: str) -> str:
    resolved = commit(path, text, f"merge, resolving {path}")
    assert len(git("rev-list", "--parents", "-n1", resolved).split()) == 3  # a merge commit
    return resolved


def resolve_conflict(ref: str, path: str, text: str) -> str:
    assert subprocess.run(["git", "merge", "-q", ref], capture_output=True).returncode == 1  # conflicts
    return continue_after_resolving(path, text)


def test_a_merge_that_resolved_code_by_hand_needs_review(repo):
    record_fix(repo, LOW, [commit("shared.py", "b\n", "a fix that edits shared.py")])
    advance_main("shared.py", "c\n")
    resolved = resolve_conflict("origin/main", "shared.py", "b and c\n")
    assert unreviewed(repo) == [resolved]


def test_the_navari_4890_shape_needs_no_second_round(repo):
    """A low fix of a decision doc, then a merge of the parent that resolved a conflict in it."""
    record_fix(repo, LOW, [commit("docs/D-x.md", "shared on a live site\n", "fix the decision doc")])
    advance_main("docs/D-x.md", "only a site's CRAs decide\n")
    resolve_conflict("origin/main", "docs/D-x.md", "only a site's CRAs decide; shared on a live site\n")
    record_fix(repo, LOW, [commit("tests/test_x.py", "def test(): ...\n", "rename a misnamed test")])
    assert unreviewed(repo) == []


def test_new_doc_and_test_commits_need_no_review_but_code_does(repo):
    commit("docs/guide.md", "how\n", "document it")
    code = commit("app.py", "print()\n", "more code")
    assert unreviewed(repo) == [code]


def test_the_tail_of_a_squash_merged_parent_counts_as_base(repo, monkeypatch):
    """Stacked on `parent`, which merged into main as one squashed commit and was deleted: merging
    the parent's last commits in brought in code its own PR reviewed, not this one's."""
    git("checkout", "-q", "-b", "parent", "main")
    tail = commit("parent.py", "parent code\n", "the parent's last commit")
    git("checkout", "-q", "feat")
    merge(tail)
    repo["base"] = "parent"
    monkeypatch.setattr(github, "merged_pr_for_branch", lambda branch: 8 if branch == "parent" else None)
    monkeypatch.setattr(feature, "fetch_pr_head", lambda number: None)
    monkeypatch.setattr(github, "pr_head_sha", lambda number: tail)
    assert unreviewed(repo) == []


def merge_another_branch() -> str:
    git("checkout", "-q", "-b", "other", "main")
    commit("other.py", "unreviewed code\n", "another branch's work")
    git("checkout", "-q", "feat")
    return merge("other")


def test_a_merge_of_a_branch_other_than_the_base_needs_review(repo):
    merged = merge_another_branch()
    assert unreviewed(repo) == [merged]


def test_a_review_of_the_head_needs_no_walk(repo, monkeypatch):
    monkeypatch.setattr(feature, "commits_since", lambda *a: pytest.fail("walked a covered head"))
    assert unreviewed(repo) == []


@pytest.fixture
def sync(repo, monkeypatch):
    record_review(repo, sha=git("rev-parse", "HEAD"), new_blocking=0, new_low=0, regressions=0, summary="clean")
    monkeypatch.setattr(feature, "resolve_issue", lambda branch: 7)
    monkeypatch.setattr(feature, "load_state", lambda branch, raw=False: (7, repo, ""))
    monkeypatch.setattr(github, "comment", lambda issue, note: None)
    monkeypatch.setattr(cli, "_save", lambda change, published=None: None)

    def run(git_town):
        monkeypatch.setattr(cli, "git_town_sync", lambda stack: git_town())
        cli.cmd_sync(Namespace(branch="feat", stack=False))

    return run


def stop_at_conflict():
    assert subprocess.run(["git", "merge", "-q", "origin/main"], capture_output=True).returncode == 1
    raise CommandFailed(1, ["git", "town", "sync"], "", "CONFLICT")


def test_a_rerun_after_a_conflicted_sync_invalidates_the_review(repo, sync):
    commit("shared.py", "b\n", "edit shared.py")
    advance_main("shared.py", "c\n")
    with pytest.raises(CommandFailed):
        sync(stop_at_conflict)
    continue_after_resolving("shared.py", "b and c\n")
    sync(lambda: "")
    assert repo["last_review"] is None
    assert [entry.get("event") for entry in repo["review_history"]] == [None, "base-advanced"]
    sync(lambda: "")
    assert [entry.get("event") for entry in repo["review_history"]] == [None, "base-advanced"]


def test_a_clean_merge_of_the_base_keeps_the_review(repo, sync):
    advance_main("other.py", "x\n")
    sync(lambda: merge("origin/main"))
    assert repo["last_review"] is not None
    assert len(repo["review_history"]) == 1


def test_sync_leaves_a_merge_of_another_branch_to_the_coverage_rule(repo, sync):
    merged = merge_another_branch()
    sync(lambda: "")
    assert len(repo["review_history"]) == 1
    assert unreviewed(repo) == [merged]
