"""`feature tidy` against real git repos: what it removes, and what makes it keep a checkout untouched."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ship import __main__ as cli
from ship import feature, github, tidy
from ship.shell import CommandFailed
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


# A container bind-mounting a host folder writes there as its own user: root, or a uid the host has no account for.
# Only a Linux host keeps that ownership; Docker Desktop's mounts give the files to the user.
docker = pytest.mark.skipif(
    sys.platform != "linux"
    or shutil.which("docker") is None
    or subprocess.run(["docker", "info"], check=False, capture_output=True).returncode != 0,
    reason="needs docker on a Linux host",
)


@pytest.fixture
def container(repo):
    """Run a shell script as root in a container with the checkout's `logs/` at /d; give it all back to us after."""

    def as_root(script: str) -> None:
        mount = f"{repo[1] / 'logs'}:/d"
        subprocess.run(["docker", "run", "--rm", "-v", mount, "alpine", "sh", "-c", script], check=True)

    yield as_root
    as_root(f"chown -R {os.getuid()}:{os.getgid()} /d && chmod -R u+rwx /d")


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


def test_git_s_own_check_keeps_an_edit_made_after_tidy_s(repo, monkeypatch):
    main, checkout = repo
    checked = tidy.check

    def then_edited(*args):  # another session writes to the checkout between tidy's check and the removal
        found = checked(*args)
        (checkout / "app.py").write_text("edited meanwhile\n")
        return found

    monkeypatch.setattr(tidy, "check", then_edited)
    with pytest.raises(CommandFailed, match="contains modified or untracked files"):
        cli.main(["tidy", "--branch", "feat"])
    assert (checkout / "app.py").read_text() == "edited meanwhile\n"
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


@docker
def test_removes_nothing_while_a_sticky_folder_holds_files_someone_else_owns(repo, container):
    main, checkout = repo
    # World-writable, but sticky: only a file's owner, or the folder's, may delete the file.
    container("mkdir -m 1777 /d/shared && echo up > /d/shared/backend.log")
    assert str(checkout / "logs" / "shared" / "backend.log") in _refused()
    _kept(main, checkout)


@docker
@pytest.mark.parametrize("mode", ["755", "700"], ids=["can't empty", "can't read"])
def test_names_the_uid_of_a_folder_s_owner_the_host_has_no_account_for(repo, container, mode):
    main, checkout = repo
    container(f"mkdir -m {mode} /d/grafana && echo x > /d/grafana/db && chown -R 42424242 /d/grafana")
    assert f"{checkout / 'logs' / 'grafana'} (owned by uid 42424242)" in _refused()
    _kept(main, checkout)


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


def test_keeps_a_checkout_holding_another_worktree(repo):
    main, checkout = repo
    nested = checkout / ".claude" / "worktrees" / "child"  # .claude/ is ignored, and the main checkout has it too
    _git("worktree", "add", "-q", "-b", "child", str(nested), cwd=main)
    (nested / "unsaved.md").write_text("important work\n")
    assert ".claude/worktrees/child" in _refused()
    _kept(main, checkout)
    assert (nested / "unsaved.md").is_file()


def test_a_registered_worktree_cannot_be_accepted(repo):
    main, checkout = repo
    nested = checkout / ".claude" / "worktrees" / "child"
    _git("worktree", "add", "-q", "-b", "child", str(nested), cwd=main)
    assert "`git worktree remove` it first" in _refused("--accept", ".claude/worktrees/child")
    _kept(main, checkout)


def test_a_worktree_another_repository_registered_cannot_be_accepted(repo, tmp_path):
    main, checkout = repo
    other = tmp_path / "other"
    other.mkdir()
    _git("init", "-q", "-b", "main", cwd=other)
    _git("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "o", cwd=other)
    _git("worktree", "add", "-q", "-b", "side", str(checkout / "logs" / "other-worktree"), cwd=other)
    assert "`git worktree remove` it first" in _refused("--accept", "logs/other-worktree")
    _kept(main, checkout)
    assert str(checkout / "logs" / "other-worktree") in _git("worktree", "list", cwd=other)


def test_keeps_a_checkout_holding_a_repo_of_its_own_until_the_user_accepts_it(repo):
    main, checkout = repo
    nested = checkout / "logs" / "clone"  # logs/ is ignored, and the main checkout has it too
    nested.mkdir()
    _git("init", "-q", cwd=nested)
    assert "logs/clone" in _refused()
    _kept(main, checkout)
    cli.main(["tidy", "--branch", "feat", "--accept", "logs/clone"])
    assert not checkout.exists()


def test_keeps_a_checkout_holding_a_bare_repo(repo):
    main, checkout = repo
    backup = checkout / "logs" / "backup.git"
    _git("init", "-q", "--bare", str(backup), cwd=main)
    assert "logs/backup.git" in _refused()
    _kept(main, checkout)


def test_keeps_a_repo_in_a_cache_whatever_state_it_is_in(repo):
    main, checkout = repo
    editable = checkout / ".venv" / "src" / "editable"  # where `pip install -e git+…` clones, to be edited
    editable.mkdir(parents=True)
    _git("init", "-q", cwd=editable)
    (editable / "code.py").write_text("uncommitted edit\n")
    assert ".venv/src/editable" in _refused()
    _kept(main, checkout)
    assert (editable / "code.py").is_file()


def test_finds_a_repo_inside_a_nested_repo(repo):
    main, checkout = repo
    outer = checkout / "logs" / "clone"
    (outer / ".venv" / "inner").mkdir(parents=True)
    _git("init", "-q", cwd=outer)
    _git("init", "-q", cwd=outer / ".venv" / "inner")
    refused = _refused("--accept", "logs/clone")
    assert "logs/clone/.venv/inner" in refused
    _kept(main, checkout)


def test_a_git_dir_s_internals_are_not_taken_for_repos(repo):
    main, checkout = repo
    clone = checkout / "logs" / "clone"
    clone.mkdir()
    _git("init", "-q", cwd=clone)
    (clone / ".git" / "modules" / "sub").mkdir(parents=True)
    _git("init", "-q", "--bare", str(clone / ".git" / "modules" / "sub"), cwd=main)  # a submodule's git dir
    assert "modules" not in _refused()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can empty any folder")
def test_a_folder_that_cannot_be_emptied_inside_a_nested_repo_blocks(repo):
    main, checkout = repo
    clone = checkout / "logs" / "clone"
    clone.mkdir()
    _git("init", "-q", cwd=clone)
    stuck = clone / ".git" / "objects"
    stuck.chmod(0o555)
    try:
        assert str(stuck) in _refused("--accept", "logs/clone")
        _kept(main, checkout)
    finally:
        stuck.chmod(0o755)


@pytest.mark.parametrize(
    "pointer",
    ["gitdir: ../../../.git/modules/src/pkg/static\n", ""],
    ids=["dangling submodule pointer", "empty gitfile"],
)
def test_a_git_file_git_cannot_open_goes_with_its_cache(repo, pointer):
    main, checkout = repo
    package = checkout / ".venv" / "lib" / "site-packages" / "pkg" / "static"
    package.mkdir(parents=True)
    (package / ".git").write_text(pointer)  # what pygaljs ships: a submodule's pointer, its target never packaged
    cli.main(["tidy", "--branch", "feat"])
    assert not checkout.exists()


def test_a_git_file_git_cannot_open_outside_a_cache_blocks(repo):
    main, checkout = repo
    leftover = checkout / ".claude" / "worktrees" / "moved"  # a worktree moved by hand, its registration pruned
    leftover.mkdir(parents=True)
    (leftover / ".git").write_text("gitdir: /nowhere/.git/worktrees/moved\n")
    (leftover / "notes.md").write_text("important work\n")
    assert ".claude/worktrees/moved" in _refused()
    _kept(main, checkout)


def test_accepts_an_ignored_folder_as_tidy_prints_it(repo):
    main, checkout = repo
    (checkout / "results").mkdir()
    (checkout / "results" / "run1.json").write_text("{}\n")
    assert "results/" in _refused()
    cli.main(["tidy", "--branch", "feat", "--accept", "results/"])
    assert not checkout.exists()


def test_a_submodule_goes_once_the_user_accepts_it(repo, tmp_path, monkeypatch):
    main, checkout = repo
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git("init", "-q", "-b", "main", cwd=upstream)
    _git("-c", "user.name=t", "-c", "user.email=t@e", "commit", "-q", "--allow-empty", "-m", "u", cwd=upstream)
    _git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(upstream), "sub", cwd=checkout)
    _git("commit", "-q", "-m", "sub", cwd=checkout)
    head = _git("rev-parse", "HEAD", cwd=checkout)
    monkeypatch.setattr(github, "pr_head_sha", lambda number: head)
    assert "sub" in _refused()
    _kept(main, checkout)
    cli.main(["tidy", "--branch", "feat", "--accept", "sub"])
    assert not checkout.exists()
