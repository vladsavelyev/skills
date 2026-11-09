"""`fleet`: what of CLAUDE.md and the settings it copies, and what each machine keeps."""

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


def _home(tmp_path, monkeypatch, *, imports: bool = True, rules: bool = True):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    shared = f"@~/{AGENTS}\n" if imports else "rules inline\n"
    (home / fleet.CLAUDE_MD).write_text(shared + fleet.MARKER + "\nmine\n")
    if rules:
        (home / AGENTS).parent.mkdir(parents=True)
        (home / AGENTS).write_text("rules\n")
    return home


def _sync(apply: bool) -> fleet.Report:
    report = fleet.Report()
    fleet.sync_agents_md([fleet.Machine(None)], fleet.Config(hosts=["u@h"], agents_md=AGENTS), apply, report)
    return report


def test_agents_md_without_codex_needs_nothing(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    assert _sync(apply=True).unresolved == 0
    assert not (home / ".codex").exists()


def test_agents_md_replaces_codex_file_with_a_link_and_keeps_the_old_one(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / ".codex").mkdir()
    (home / fleet.CODEX_AGENTS_MD).write_text("old codex rules\n")
    (home / fleet.CODEX_CONFIG).write_text('model = "m"\n')
    assert _sync(apply=False).unresolved == 2  # the link and the instructions
    assert _sync(apply=True).unresolved == 0
    link = home / fleet.CODEX_AGENTS_MD
    assert link.is_symlink() and link.resolve() == (home / AGENTS).resolve()
    [backup] = (home / ".codex").glob("AGENTS.md.bak-*")
    assert backup.read_text() == "old codex rules\n"
    assert _sync(apply=False).unresolved == 0


def test_agents_md_flags_a_missing_import_or_file(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch, imports=False, rules=False)
    assert _sync(apply=True).unresolved == 2


OWN = "### This machine\n\nThis machine is `zebra`.\n"


def test_codex_instructions_go_first_and_round_trip():
    config = 'model = "m"\n\n[model_providers.x]\nname = "X"\n'
    new = fleet.with_codex_instructions(config, OWN, "here")
    assert new.startswith(fleet.CODEX_BLOCK_START)
    parsed = fleet.tomllib.loads(new)
    assert parsed["developer_instructions"] == OWN
    assert parsed["model"] == "m" and parsed["model_providers"]["x"]["name"] == "X"
    assert fleet.with_codex_instructions(new, OWN, "here") == new


def test_codex_instructions_replace_only_fleets_block():
    first = fleet.with_codex_instructions('model = "m"\n', "old\n", "here")
    new = fleet.with_codex_instructions(first, OWN, "here")
    assert "old" not in new and new.count(fleet.CODEX_BLOCK_START) == 1
    assert fleet.tomllib.loads(new)["developer_instructions"] == OWN


def test_codex_instructions_refuse_a_hand_set_key_and_a_triple_quote():
    with pytest.raises(RuntimeError):
        fleet.with_codex_instructions('developer_instructions = "mine"\n', OWN, "here")
    with pytest.raises(RuntimeError):
        fleet.with_codex_instructions("", "has ''' in it\n", "here")


def test_agents_md_puts_the_machine_part_into_codex_config(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    (home / ".codex").mkdir()
    (home / fleet.CODEX_CONFIG).write_text('model = "m"\n')
    assert _sync(apply=False).unresolved == 2  # the link and the instructions
    assert _sync(apply=True).unresolved == 0
    assert fleet.tomllib.loads((home / fleet.CODEX_CONFIG).read_text())["developer_instructions"] == "mine\n"
    assert _sync(apply=False).unresolved == 0
