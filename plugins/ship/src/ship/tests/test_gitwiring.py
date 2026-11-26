"""Tests for git wiring helpers with branching logic worth pinning."""

from ship import gitwiring


def _fake_git(local_exists: bool, ahead: str):
    def fake_run(cmd, cwd=None):
        if "for-each-ref" in cmd:
            return "refs/heads/main" if local_exists else ""
        if "rev-list" in cmd:
            assert "origin/main..main" in cmd  # against the remote ref, never the upstream config
            return ahead
        raise AssertionError(f"unexpected cmd: {cmd}")

    return fake_run


def test_local_only_commits_empty_without_a_local_base(monkeypatch):
    monkeypatch.setattr(gitwiring, "run", _fake_git(local_exists=False, ahead="x"))
    assert gitwiring.local_only_commits("main", "origin/main") == []


def test_local_only_commits_empty_when_in_sync(monkeypatch):
    monkeypatch.setattr(gitwiring, "run", _fake_git(local_exists=True, ahead=""))
    assert gitwiring.local_only_commits("main", "origin/main") == []


def test_local_only_commits_lists_unpushed_even_without_upstream(monkeypatch):
    monkeypatch.setattr(gitwiring, "run", _fake_git(local_exists=True, ahead="abc1234\ndef5678"))
    assert gitwiring.local_only_commits("main", "origin/main") == ["abc1234", "def5678"]


def _repo(tmp_path, monkeypatch):
    """A git repo whose config sees nothing of the user's: no global or system file."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    gitwiring.run(["git", "init", "--quiet", str(tmp_path / "repo")])
    return str(tmp_path / "repo")


def test_reviewer_defaults_to_codex_sol(tmp_path, monkeypatch):
    assert gitwiring.reviewer(cwd=_repo(tmp_path, monkeypatch)) == ("codex", "sol")


def test_reviewer_reads_each_key_on_its_own(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    gitwiring.run(["git", "config", "feature.reviewer-model", "opus"], cwd=repo)
    assert gitwiring.reviewer(cwd=repo) == ("codex", "opus")
    gitwiring.run(["git", "config", "--global", "feature.reviewer-provider", "claude"], cwd=repo)
    assert gitwiring.reviewer(cwd=repo) == ("claude", "opus")
