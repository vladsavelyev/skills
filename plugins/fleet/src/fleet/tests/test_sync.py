"""`fleet`: what of CLAUDE.md and the settings it copies, and what each machine keeps."""

import subprocess

import pytest

from fleet import sync as fleet

SHARED = "# Rules\n\nshared text\n\n" + fleet.MARKER + "\n"


def test_split_claude_md_keeps_the_marker_with_the_shared_part():
    shared, own = fleet.split_claude_md(SHARED + "\n### This machine\nlocal\n", "here")
    assert shared == SHARED
    assert own == "\n### This machine\nlocal\n"


def test_split_claude_md_without_a_marker_refuses():
    with pytest.raises(RuntimeError, match="has no line"):
        fleet.split_claude_md("# Rules\n### This machine\n", "host")


def test_merge_settings_copies_shared_keys_and_keeps_machine_ones():
    source = {
        "model": "opus",
        "env": {"KEY": "new", "ONLY_HERE": "x"},
        "hooks": {"Stop": ["osascript"], "PostToolUse": ["fmt"]},
        "statusLine": {"type": "command", "command": "bash-only"},
    }
    target = {
        "model": "sonnet",
        "env": {"KEY": "old", "DISPLAY": ":99", "STALE": "y"},
        "hooks": {"PostToolUse": ["old-fmt"]},
        "statusLine": {"type": "command", "command": "posix"},
        "theme": "dark",
    }
    merged = fleet.merge_settings(source, target, ["env.DISPLAY", "hooks.Stop", "statusLine"])
    assert merged == {
        "model": "opus",
        "env": {"KEY": "new", "ONLY_HERE": "x", "DISPLAY": ":99"},
        "hooks": {"PostToolUse": ["fmt"]},
        "statusLine": {"type": "command", "command": "posix"},
    }


def test_merge_settings_of_identical_files_is_a_no_op():
    settings = {"a": 1, "env": {"X": "1"}, "permissions": {"deny": ["rm"]}}
    assert fleet.flatten(fleet.merge_settings(settings, settings, [])) == fleet.flatten(settings)


def test_merge_settings_keeps_an_empty_object_from_the_source():
    assert fleet.merge_settings({"env": {}}, {"env": {"X": "1"}}, []) == {"env": {}}


def test_load_config_without_a_file_says_what_to_create():
    with pytest.raises(RuntimeError, match="no fleet configured"):
        fleet.load_config()


def test_load_config_without_hosts_refuses():
    fleet.CONFIG_PATH.parent.mkdir(parents=True)
    fleet.CONFIG_PATH.write_text("hosts = []\n")
    with pytest.raises(RuntimeError, match="`hosts` is empty"):
        fleet.load_config()


def test_load_config_reads_the_file():
    fleet.CONFIG_PATH.parent.mkdir(parents=True)
    fleet.CONFIG_PATH.write_text('hosts = ["u@h"]\nhelpers = ["git-pr-context"]\n')
    assert fleet.load_config() == fleet.Config(hosts=["u@h"], helpers=["git-pr-context"])


LAPTOP = fleet.Identity("laptop", "Darwin arm64", None)
EC2 = fleet.Identity("ip-10-0-0-1", "Linux x86_64", "ubuntu@ec2.example")


def test_machine_section_on_the_source_names_it_and_its_hosts():
    section = fleet.machine_section(LAPTOP, [LAPTOP, EC2])
    assert section.startswith(fleet.SECTION_START + "\n") and section.endswith(fleet.SECTION_END + "\n")
    assert "`laptop` (Darwin arm64), the fleet's source of truth" in section
    assert "- `laptop` (Darwin arm64): source of truth ← this one" in section
    assert "- `ip-10-0-0-1` (Linux x86_64): `ssh ubuntu@ec2.example`\n" in section
    assert "none of it is synced" in section


def test_machine_section_on_a_host_says_where_to_make_shared_changes():
    section = fleet.machine_section(EC2, [LAPTOP, EC2])
    assert "reached from `laptop` as `ssh ubuntu@ec2.example`" in section
    assert "make those changes on `laptop`" in section
    assert "`ssh ubuntu@ec2.example` ← this one" in section


def test_with_machine_section_puts_a_new_section_above_the_hand_written_text():
    section = fleet.machine_section(LAPTOP, [LAPTOP, EC2])
    assert fleet.with_machine_section("\n### Environments\nmine\n", section, "here") == (
        section + "\n### Environments\nmine\n"
    )


def test_with_machine_section_replaces_only_the_generated_section():
    old = fleet.machine_section(LAPTOP, [LAPTOP])
    new = fleet.machine_section(LAPTOP, [LAPTOP, EC2])
    own = "\n" + old + "\n### Environments\nmine\n"
    assert fleet.with_machine_section(own, new, "here") == "\n" + new + "\n### Environments\nmine\n"
    assert fleet.with_machine_section(fleet.with_machine_section(own, new, "here"), new, "here") == (
        fleet.with_machine_section(own, new, "here")
    )


def test_with_machine_section_without_an_end_marker_refuses():
    with pytest.raises(RuntimeError, match="but no"):
        fleet.with_machine_section(fleet.SECTION_START + "\nhalf\n", "x", "host")


AGENTS = ".claude/plugins/marketplaces/m/agents/AGENTS.md"


def _git(*args: str, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _publish(tmp_path, rules: str) -> None:
    """Commit new rules to the repo every machine clones them from."""
    origin = tmp_path / "origin"
    (origin / "agents" / "AGENTS.md").write_text(rules)
    _git("commit", "-qam", "rules", cwd=origin)


def _home(tmp_path, monkeypatch, *, imports: bool = True, rules: bool = True):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "t")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@example.com")
    shared = f"@~/{AGENTS}\n" if imports else "rules inline\n"
    (home / fleet.CLAUDE_MD).write_text(shared + fleet.MARKER + "\nmine\n")
    if rules:
        origin = tmp_path / "origin"
        (origin / "agents").mkdir(parents=True)
        (origin / "agents" / "AGENTS.md").write_text("rules\n")
        _git("init", "-q", "-b", "main", cwd=origin)
        _git("add", "-A", cwd=origin)
        _git("commit", "-qm", "rules", cwd=origin)
        _git("clone", "-q", str(origin), str((home / AGENTS).parent.parent), cwd=tmp_path)
    return home


def _sync(apply: bool) -> fleet.Report:
    report = fleet.Report()
    fleet.sync_agents_md([fleet.Machine(None)], fleet.Config(hosts=["u@h"], agents_md=[AGENTS]), apply, report)
    return report


def test_agents_md_without_codex_needs_nothing(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    assert _sync(apply=True).unresolved == 0
    assert not (home / ".codex").exists()


def _codex(home) -> str:
    return (home / fleet.CODEX_AGENTS_MD).read_text()


def test_agents_md_generates_codex_rules_with_the_machine_part(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / ".codex").mkdir()
    assert _sync(apply=False).unresolved == 1
    assert _sync(apply=True).unresolved == 0
    assert _codex(home) == fleet.codex_agents_md([AGENTS], ["rules\n"], "mine\n")
    assert _codex(home).endswith("\n\nrules\n\nmine\n")
    assert not list((home / ".codex").glob("AGENTS.md.bak-*"))
    assert _sync(apply=False).unresolved == 0


def test_agents_md_regenerates_codex_rules_when_either_half_changes(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / ".codex").mkdir()
    _sync(apply=True)
    _publish(tmp_path, "new rules\n")
    # The clone is behind; its Codex copy waits for the fast-forward.
    assert _sync(apply=False).unresolved == 1
    _sync(apply=True)
    (home / fleet.CLAUDE_MD).write_text(f"@~/{AGENTS}\n" + fleet.MARKER + "\nmoved\n")
    assert _sync(apply=False).unresolved == 1
    _sync(apply=True)
    assert _codex(home).endswith("\n\nnew rules\n\nmoved\n")


def test_agents_md_keeps_a_hand_written_codex_file(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / ".codex").mkdir()
    (home / fleet.CODEX_AGENTS_MD).write_text("old codex rules\n")
    _sync(apply=True)
    [backup] = (home / ".codex").glob("AGENTS.md.bak-*")
    assert backup.read_text() == "old codex rules\n"


def test_agents_md_replaces_a_codex_symlink_and_leaves_its_target(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / ".codex").mkdir()
    (home / fleet.CODEX_AGENTS_MD).symlink_to(home / AGENTS)
    assert _sync(apply=False).unresolved == 1
    _sync(apply=True)
    assert not (home / fleet.CODEX_AGENTS_MD).is_symlink()
    assert (home / AGENTS).read_text() == "rules\n"
    assert not list((home / ".codex").glob("AGENTS.md.bak-*"))
    assert _sync(apply=False).unresolved == 0


def test_agents_md_flags_a_missing_import_or_file(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, imports=False, rules=False)
    assert _sync(apply=True).unresolved == 2


def test_agents_md_fast_forwards_a_clone_behind_before_generating_codex_rules(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / ".codex").mkdir()
    _publish(tmp_path, "new rules\n")
    assert _sync(apply=True).unresolved == 0
    assert (home / AGENTS).read_text() == "new rules\n"
    assert _codex(home).endswith("\n\nnew rules\n\nmine\n")


def test_agents_md_in_a_modified_clone_is_a_problem_and_not_copied(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    (home / ".codex").mkdir()
    (home / AGENTS).write_text("edited here\n")
    assert _sync(apply=True).unresolved == 1
    assert "M agents/AGENTS.md" in capsys.readouterr().out
    assert not (home / fleet.CODEX_AGENTS_MD).exists()


def test_agents_md_in_a_clone_ahead_of_its_repo_is_a_problem_and_not_copied(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / ".codex").mkdir()
    clone = (home / AGENTS).parent.parent
    (home / AGENTS).write_text("committed here only\n")
    _git("commit", "-qam", "local", cwd=clone)
    assert _sync(apply=True).unresolved == 1
    assert not (home / fleet.CODEX_AGENTS_MD).exists()


def test_agents_md_removed_upstream_is_a_problem(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _git("rm", "-q", "agents/AGENTS.md", cwd=tmp_path / "origin")
    _git("commit", "-qm", "moved", cwd=tmp_path / "origin")
    assert _sync(apply=True).unresolved == 1
    assert not (home / AGENTS).exists()


def test_agents_md_in_a_linked_worktree_is_synced_like_a_clone(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    clone = (home / AGENTS).parent.parent
    clone.rename(tmp_path / "main-clone")
    _git("worktree", "add", "-q", "--detach", str(clone), cwd=tmp_path / "main-clone")
    _git("switch", "-q", "-C", "main", "--ignore-other-worktrees", cwd=clone)
    (home / ".codex").mkdir()
    _publish(tmp_path, "new rules\n")
    assert _sync(apply=True).unresolved == 0
    assert _codex(home).endswith("\n\nnew rules\n\nmine\n")


def test_agents_md_outside_git_is_a_problem(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch, rules=False)
    (home / AGENTS).parent.mkdir(parents=True)
    (home / AGENTS).write_text("rules\n")
    assert _sync(apply=True).unresolved == 1


HOME_AGENTS = "git/home/AGENTS.md"


class _Host(fleet.Machine):
    """A host whose home directory is a local one, so a test reaches it without ssh."""

    def __init__(self, home):
        super().__init__("host")
        self.home = home

    def run(self, argv, input=None, check=True):
        proc = fleet.run(argv, cwd=self.home, check=False, input=input or "")
        if check and proc.returncode != 0:
            raise RuntimeError(f"host: `{argv}` failed: {proc.stderr}")
        return proc


def _home_repo(tmp_path, home) -> None:
    """A second rules repo, published and cloned to this machine at ~/git/home."""
    origin = tmp_path / "home-origin"
    origin.mkdir()
    (origin / "AGENTS.md").write_text("pointers\n")
    _git("init", "-q", "-b", "main", cwd=origin)
    _git("add", "-A", cwd=origin)
    _git("commit", "-qm", "pointers", cwd=origin)
    _git("clone", "-q", str(origin), str(home / "git" / "home"), cwd=tmp_path)
    (home / fleet.CLAUDE_MD).write_text(f"@~/{AGENTS}\n@~/{HOME_AGENTS}\n" + fleet.MARKER + "\nmine\n")


def _sync_all(machines, apply: bool) -> fleet.Report:
    report = fleet.Report()
    config = fleet.Config(hosts=["u@h"], agents_md=[AGENTS, HOME_AGENTS])
    fleet.sync_agents_md(machines, config, apply, report)
    return report


def test_agents_md_joins_every_file_in_order_for_codex(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _home_repo(tmp_path, home)
    (home / ".codex").mkdir()
    assert _sync_all([fleet.Machine(None)], apply=True).unresolved == 0
    assert _codex(home).endswith("\n\nrules\n\npointers\n\nmine\n")
    assert f"~/{AGENTS}, ~/{HOME_AGENTS}" in _codex(home).splitlines()[0]


def test_agents_md_clones_each_repo_on_a_host_that_lacks_it(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _home_repo(tmp_path, home)
    host_home = tmp_path / "host"
    (host_home / ".codex").mkdir(parents=True)
    (host_home / ".claude").mkdir()
    (host_home / fleet.CLAUDE_MD).write_text(fleet.MARKER + "\nthe host's own\n")
    host = _Host(host_home)
    machines = [fleet.Machine(None), host]
    assert _sync_all(machines, apply=False).unresolved == 2
    assert not (host_home / "git").exists()
    assert _sync_all(machines, apply=True).unresolved == 0
    assert (host_home / HOME_AGENTS).read_text() == "pointers\n"
    assert (host_home / AGENTS).read_text() == "rules\n"
    assert (host_home / fleet.CODEX_AGENTS_MD).read_text().endswith("\n\nrules\n\npointers\n\nthe host's own\n")
    assert _sync_all(machines, apply=False).unresolved == 0


def test_agents_md_skips_codex_where_any_file_is_blocked(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    _home_repo(tmp_path, home)
    (home / ".codex").mkdir()
    (home / HOME_AGENTS).write_text("edited here\n")
    assert _sync_all([fleet.Machine(None)], apply=True).unresolved == 1
    assert not (home / fleet.CODEX_AGENTS_MD).exists()


def test_load_config_refuses_a_single_agents_md_path(tmp_path):
    fleet.CONFIG_PATH.parent.mkdir(parents=True)
    fleet.CONFIG_PATH.write_text(f'hosts = ["u@h"]\nagents_md = "{AGENTS}"\n')
    with pytest.raises(RuntimeError, match="must be a list"):
        fleet.load_config()
