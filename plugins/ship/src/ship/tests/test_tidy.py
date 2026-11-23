"""`feature tidy` against real git repos: what it removes, and what makes it keep a checkout untouched."""

import os
import subprocess
from pathlib import Path

import pytest

from ship import __main__ as cli
from ship import feature, github
from ship.state import new_state


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A main checkout with what running a project leaves in it, and a merged feature `feat` in its own worktree."""
    main = tmp_path / "repo"
    main.mkdir()
    _git("init", "-q", "-b", "main", cwd=main)
    _git("config", "user.email", "t@example.com", cwd=main)
    _git("config", "user.name", "t", cwd=main)
    (main / ".gitignore").write_text(".claude/\n.env\n.venv/\nnode_modules/\nlogs/\nresults/\n")
    _git("add", ".gitignore", cwd=main)
    _git("commit", "-q", "-m", "init", cwd=main)
    (main / ".env").write_text("A=1\n")
    (main / "logs").mkdir()
    checkout = main / ".claude" / "worktrees" / "feat"
    _git("worktree", "add", "-q", "-b", "feat", str(checkout), cwd=main)
    _git("config", "git-town-branch.feat.parent", "main", cwd=main)
    (checkout / "app.py").write_text("print(1)\n")
    _git("add", "app.py", cwd=checkout)
    _git("commit", "-q", "-m", "feat", cwd=checkout)
    # What running the project made in the feature's checkout.
    (checkout / ".env").write_text("A=2\n")
    (checkout / "logs" / "containers").mkdir(parents=True)
    (checkout / "logs" / "containers" / "backend.log").write_text("up\n")
    (checkout / ".venv" / "lib").mkdir(parents=True)
    (checkout / "node_modules" / "x").mkdir(parents=True)

    head = _git("rev-parse", "HEAD", cwd=checkout)
    state = new_state(branch="feat", base="main", updated="2026-11-23T00:00:00Z")
    state["pr"] = 5
    decision = cli.gate.GateDecision(verdict=cli.gate.Verdict.MERGED, reasons=[], next_step="", rounds_used=1, budget=2)
    monkeypatch.setattr(feature, "gate_for", lambda branch: (7, state, [], decision))
    monkeypatch.setattr(github, "pr_head_sha", lambda number: head)
    monkeypatch.setattr(github, "use_owner_account", lambda: None)
    monkeypatch.chdir(main)
    return main, checkout


def _branches(main: Path) -> list[str]:
    return _git("branch", "--format=%(refname:short)", cwd=main).split()


def _refused(*argv: str) -> str:
    with pytest.raises(SystemExit) as refused:
        cli.main(["tidy", "--branch", "feat", *argv])
    return str(refused.value.code)


def _kept(main: Path, checkout: Path) -> None:
    assert (checkout / "app.py").is_file()
    assert str(checkout) in _git("worktree", "list", cwd=main)
    assert "feat" in _branches(main)


def test_removes_the_checkout_and_branch_when_all_it_holds_is_what_running_the_project_made(repo, capsys):
    main, checkout = repo
    cli.main(["tidy", "--branch", "feat"])
    assert not checkout.exists()
    assert "feat" not in _branches(main)
    pointers = subprocess.run(["git", "config", "--get-regexp", "git-town"], cwd=main, capture_output=True, text=True)
    assert pointers.returncode == 1  # no parent pointer left behind
    assert "Deleted the branch feat" in capsys.readouterr().out


def test_keeps_a_checkout_with_an_untracked_file(repo):
    main, checkout = repo
    (checkout / "notes.md").write_text("todo\n")
    assert "notes.md" in _refused()
    _kept(main, checkout)


def test_keeps_a_checkout_whose_head_the_pr_does_not_have(repo):
    main, checkout = repo
    (checkout / "app.py").write_text("print(2)\n")
    _git("commit", "-q", "-am", "unpushed", cwd=checkout)
    assert "not the PR's head" in _refused()
    _kept(main, checkout)


def test_keeps_an_ignored_path_nothing_explains_until_the_user_accepts_it(repo):
    main, checkout = repo
    (checkout / "results").mkdir()
    (checkout / "results" / "run1.json").write_text("{}\n")
    assert "results/" in _refused()
    _kept(main, checkout)
    cli.main(["tidy", "--branch", "feat", "--accept", "results"])
    assert not checkout.exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can empty any folder")
def test_removes_nothing_while_a_folder_cannot_be_emptied(repo):
    main, checkout = repo
    stuck = checkout / "logs" / "containers"
    stuck.chmod(0o555)  # what a root-owned folder looks like to us: we can't delete what's in it
    try:
        assert str(stuck) in _refused()
        _kept(main, checkout)
        assert (stuck / "backend.log").is_file()
    finally:
        stuck.chmod(0o755)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read any folder")
def test_removes_nothing_while_a_folder_cannot_be_read(repo):
    main, checkout = repo
    stuck = checkout / "logs" / "containers"
    stuck.chmod(0)  # what a container's 0700 folder under another uid looks like: we can't even list it
    try:
        assert str(stuck) in _refused()
        _kept(main, checkout)
    finally:
        stuck.chmod(0o755)
    assert (stuck / "backend.log").is_file()


def test_keeps_a_branch_another_is_stacked_on(repo, capsys):
    main, checkout = repo
    _git("branch", "child", "feat", cwd=main)
    _git("config", "git-town-branch.child.parent", "feat", cwd=main)
    cli.main(["tidy", "--branch", "feat"])
    assert not checkout.exists()
    assert "feat" in _branches(main)
    assert "child is stacked on it" in capsys.readouterr().out


def test_refuses_before_the_merge(repo, monkeypatch):
    main, checkout = repo
    state = new_state(branch="feat", base="main", updated="2026-11-23T00:00:00Z")
    state["pr"] = 5
    open_ = cli.gate.GateDecision(verdict=cli.gate.Verdict.OPEN, reasons=[], next_step="", rounds_used=1, budget=2)
    monkeypatch.setattr(feature, "gate_for", lambda branch: (7, state, [], open_))
    assert "hasn't merged" in _refused()
    _kept(main, checkout)


def test_a_branch_in_the_main_checkout_goes_once_the_main_checkout_is_back_on_the_base(repo):
    main, checkout = repo
    _git("worktree", "remove", "--force", str(checkout), cwd=main)
    _git("checkout", "-q", "feat", cwd=main)
    assert "run `feature tidy --branch feat` again" in _refused()
    assert "feat" in _branches(main)
    _git("checkout", "-q", "main", cwd=main)
    cli.main(["tidy", "--branch", "feat"])
    assert "feat" not in _branches(main)
