"""`fleet`: keep each machine's per-user Claude Code setup in line with this one.

A T3 thread on a remote machine runs that machine's Claude Code, which reads only that machine's
`~/.claude`. This machine is the source of truth, and the hosts in CONFIG_PATH follow
it. A check reports drift; `--apply` fixes what can be fixed without a human:

- Claude Code: every machine on the newest version any of them runs.
- Skill repos: the git clones `~/.claude/skills` links into, fast-forwarded to their default branch on
  every machine, and the same links on the hosts.
- Plugins: this machine's enabled user-scope plugins installed, enabled and updated on the hosts.
- `~/.claude/CLAUDE.md`: everything above MARKER copied; below it each machine describes itself, starting
  with a generated section naming the machine, the fleet, and that its memory is its own.
- `~/.claude/settings*.json`: every key copied, except the machine's own (`machine_settings`).
- `agents_md` (optional): the shared agent instructions file. The shared part of `~/.claude/CLAUDE.md`
  must import it, it must exist on every machine, and wherever Codex is installed
  `~/.codex/AGENTS.md` must be a symlink to it, and `~/.codex/config.toml` must carry the machine's
  own part of `~/.claude/CLAUDE.md` as `developer_instructions`.

Reported only: helper commands the statusline and hooks call (`helpers`), and T3 Code, which pins a
remote's server to the app's own version every time it connects.
"""

import difflib
import json
import re
import shlex
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .shell import out, run, try_out

CONFIG_PATH = Path.home() / ".config" / "fleet" / "config.toml"
MARKER = "<!-- fleet: everything below is this machine's own and is not synced -->"
# Below MARKER, `fleet` keeps a section saying which machine this is; the rest is hand-written.
SECTION_START = "<!-- fleet: generated machine section, rewritten by `fleet --apply` -->"
SECTION_END = "<!-- fleet: end of generated machine section -->"
CLAUDE_MD = ".claude/CLAUDE.md"
SETTINGS_FILES = (".claude/settings.json", ".claude/settings.local.json")
SKILLS_DIR = ".claude/skills"
# One connection for the whole run: a check is dozens of commands per host.
SSH_OPTS = ["-o", "ControlMaster=auto", "-o", "ControlPath=~/.ssh/fleet-%C", "-o", "ControlPersist=60"]


@dataclass
class Config:
    hosts: list[str]
    machine_settings: list[str] = field(default_factory=list)
    machine_skills: list[str] = field(default_factory=list)
    helpers: list[str] = field(default_factory=list)
    # Home-relative path of the agent instructions every machine's CLAUDE.md imports and Codex reads.
    agents_md: str | None = None


def load_config() -> Config:
    if not CONFIG_PATH.is_file():
        raise RuntimeError(
            f"no fleet configured: create {CONFIG_PATH}\n"
            '  hosts = ["user@host"]\n'
            '  machine_settings = ["env.DISPLAY", "hooks.Stop"]   # settings keys each machine keeps\n'
            '  machine_skills = ["dev-browser"]                   # skills that stay on this machine\n'
            '  helpers = ["git-pr-context"]                       # commands the hooks/statusline call\n'
            '  agents_md = ".claude/plugins/marketplaces/<m>/agents/AGENTS.md"  # optional, see README'
        )
    config = Config(**tomllib.loads(CONFIG_PATH.read_text()))
    if not config.hosts:
        raise RuntimeError(f"{CONFIG_PATH}: `hosts` is empty, so there is no fleet to sync")
    return config


class Machine:
    """Runs commands in the home directory of this machine or, over ssh, of a host.

    Paths are relative to the home directory on both, so the same argv works on either.
    """

    def __init__(self, host: str | None):
        self.host = host
        self.name = host or "this machine"

    def run(self, argv: list[str], input: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
        if self.host is None:
            cmd, cwd = argv, Path.home()
        else:
            cmd, cwd = ["ssh", *SSH_OPTS, self.host, shlex.join(argv)], None
        # stdin is always given, so nothing remote waits on a terminal that isn't there.
        proc = run(cmd, cwd=cwd, check=False, input=input or "")
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"{self.name}: `{shlex.join(argv)}` failed (exit {proc.returncode}): {proc.stderr.strip()}"
            )
        return proc

    def out(self, argv: list[str]) -> str:
        return self.run(argv).stdout.strip()

    def read(self, path: str) -> str | None:
        """A file's text, or None when it doesn't exist. Any other failure raises."""
        proc = self.run(["sh", "-c", 'if [ -e "$1" ]; then cat "$1"; else exit 3; fi', "sh", path], check=False)
        if proc.returncode == 3:
            return None
        if proc.returncode != 0:
            raise RuntimeError(f"{self.name}: cannot read {path}: {proc.stderr.strip()}")
        return proc.stdout

    def write(self, path: str, text: str) -> str:
        """Replace a file, keeping a timestamped backup of the old one. Returns the backup's path.

        The content goes over stdin, never the command line: settings carry secrets.
        """
        backup = f"{path}.bak-{datetime.now().astimezone():%Y%m%d%H%M%S}"
        script = 'cp -p "$1" "$2" && cat > "$1.fleet-new" && mv "$1.fleet-new" "$1"'
        self.run(["sh", "-c", script, "sh", path, backup], input=text)
        return backup


class Report:
    def __init__(self) -> None:
        self.unresolved = 0

    def section(self, title: str) -> None:
        print(f"\n{title}", flush=True)

    def ok(self, text: str) -> None:
        print(f"  ok      {text}", flush=True)

    def fixed(self, text: str) -> None:
        print(f"  fixed   {text}", flush=True)

    def drift(self, text: str, apply: bool) -> None:
        """Drift that --apply would fix. Without --apply it stays unresolved."""
        self.unresolved += 0 if apply else 1
        print(f"  {'fixing' if apply else 'DRIFT '}  {text}", flush=True)

    def problem(self, text: str) -> None:
        """Drift --apply can't fix: a human has to."""
        self.unresolved += 1
        print(f"  PROBLEM {text}", flush=True)

    def note(self, text: str) -> None:
        print(f"          {text}", flush=True)


# --- Claude Code ---


def claude_version(machine: Machine) -> tuple[int, ...]:
    text = machine.out(["claude", "--version"])
    match = re.match(r"(\d+(?:\.\d+)+)", text)
    if not match:
        raise RuntimeError(f"{machine.name}: unrecognised `claude --version` output: {text!r}")
    return tuple(int(p) for p in match.group(1).split("."))


def sync_claude_code(machines: list[Machine], apply: bool, report: Report) -> None:
    report.section("Claude Code")
    versions = {m.name: claude_version(m) for m in machines}
    newest = max(versions.values())
    target = ".".join(map(str, newest))
    for m in machines:
        if versions[m.name] == newest:
            report.ok(f"{m.name}: {target}")
            continue
        report.drift(f"{m.name}: {'.'.join(map(str, versions[m.name]))}, behind {target}", apply)
        if apply:
            m.run(["claude", "install", target])
            report.fixed(f"{m.name}: installed {target}")


# --- skill repos ---


@dataclass
class SkillSource:
    name: str
    repo: str  # relative to the home directory
    skill_dir: str  # relative to the repo
    url: str


def skill_sources(config: Config, report: Report) -> list[SkillSource]:
    """Where each of this machine's skills comes from. A skill outside a git repo can't be synced."""
    home = Path.home()
    sources = []
    for entry in sorted((home / SKILLS_DIR).iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.name in config.machine_skills:
            report.note(f"skill {entry.name}: this machine's own (machine_skills), not synced")
            continue
        # Either the directory is a link, or it holds a linked SKILL.md (both layouts are in use).
        skill_md = entry / "SKILL.md"
        target = skill_md.resolve().parent if skill_md.is_symlink() else entry.resolve()
        top = try_out(["git", "-C", str(target), "rev-parse", "--show-toplevel"])
        if not top:
            report.problem(f"skill {entry.name}: {target} is not in a git repo, so it can't be synced; copy it by hand")
            continue
        root = Path(top)
        if not root.is_relative_to(home):
            report.problem(f"skill {entry.name}: its repo {root} is outside the home directory")
            continue
        url = out(["git", "-C", str(root), "remote", "get-url", "origin"])
        sources.append(SkillSource(entry.name, str(root.relative_to(home)), str(target.relative_to(root)), url))
    return sources


def default_branch(machine: Machine, repo: str) -> str:
    ref = machine.run(["git", "-C", repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], check=False)
    if ref.returncode != 0:
        raise RuntimeError(
            f"{machine.name}: {repo} has no origin/HEAD; set it with `git -C {repo} remote set-head origin -a`"
        )
    return ref.stdout.strip().removeprefix("origin/")


def sync_repo(machine: Machine, repo: str, url: str, apply: bool, report: Report) -> None:
    where = f"{machine.name}: ~/{repo}"
    if machine.run(["test", "-d", f"{repo}/.git"], check=False).returncode != 0:
        report.drift(f"{where} is not cloned", apply)
        if not apply:
            return
        machine.run(["git", "clone", "-q", url, repo])
        report.fixed(f"{where}: cloned {url}")
        return
    machine.run(["git", "-C", repo, "fetch", "-q", "origin"])
    branch = default_branch(machine, repo)
    current = machine.out(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"])
    counts = machine.out(["git", "-C", repo, "rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"])
    ahead, behind = (int(n) for n in counts.split())
    if current != branch:
        report.problem(f"{where} is on {current}, not {branch}: its skills are that branch's, not {branch}'s")
        return
    if ahead:
        report.problem(f"{where} has {ahead} commit(s) not on origin/{branch}: push or drop them")
        return
    if not behind:
        report.ok(f"{where} at origin/{branch}")
        return
    if machine.out(["git", "-C", repo, "status", "--porcelain", "--untracked-files=no"]):
        report.problem(f"{where} is {behind} behind origin/{branch} but has uncommitted changes")
        return
    report.drift(f"{where} is {behind} commit(s) behind origin/{branch}", apply)
    if apply:
        machine.run(["git", "-C", repo, "merge", "-q", "--ff-only", f"origin/{branch}"])
        report.fixed(f"{where} fast-forwarded to origin/{branch}")


def sync_skill_link(machine: Machine, source: SkillSource, apply: bool, report: Report) -> None:
    link = f"{SKILLS_DIR}/{source.name}"
    want = f"{source.repo}/{source.skill_dir}"
    resolved = machine.run(["sh", "-c", 'cd "$1" 2>/dev/null && pwd -P', "sh", link], check=False).stdout.strip()
    expected = machine.run(["sh", "-c", 'cd "$1" 2>/dev/null && pwd -P', "sh", want], check=False).stdout.strip()
    if resolved and resolved == expected:
        report.ok(f"{machine.name}: skill {source.name} -> ~/{want}")
        return
    if resolved:
        report.problem(f"{machine.name}: skill {source.name} is {resolved}, not ~/{want}")
        return
    report.drift(f"{machine.name}: skill {source.name} is not linked", apply)
    if apply:
        machine.run(["mkdir", "-p", SKILLS_DIR])
        machine.run(["ln", "-s", f"{machine.out(['pwd'])}/{want}", link])
        report.fixed(f"{machine.name}: linked skill {source.name} -> ~/{want}")


def sync_skills(machines: list[Machine], config: Config, apply: bool, report: Report) -> None:
    report.section("Skill repos")
    sources = skill_sources(config, report)
    repos = {s.repo: s.url for s in sources}
    for repo, url in repos.items():
        for m in machines:
            sync_repo(m, repo, url, apply, report)
    for m in machines[1:]:
        for source in sources:
            sync_skill_link(m, source, apply, report)
        names = set(m.out(["sh", "-c", 'if [ -d "$1" ]; then ls "$1"; fi', "sh", SKILLS_DIR]).split())
        for extra in sorted(names - {s.name for s in sources} - set(config.machine_skills)):
            report.note(f"{m.name}: skill {extra} exists only there")


# --- plugins ---


def plugins(machine: Machine) -> dict[str, dict]:
    """User-scope plugins by id. Project-scope ones follow their checkout, not the machine."""
    listed = json.loads(machine.out(["claude", "plugin", "list", "--json"]))
    return {p["id"]: p for p in listed if p["scope"] == "user"}


def marketplaces(machine: Machine) -> dict[str, dict]:
    return {m["name"]: m for m in json.loads(machine.out(["claude", "plugin", "marketplace", "list", "--json"]))}


def marketplace_source(entry: dict) -> str:
    if entry.get("repo"):
        return entry["repo"]
    if entry.get("url"):
        return entry["url"]
    raise RuntimeError(f"marketplace {entry['name']} has no repo or url to add it from: {entry}")


def sync_plugins(machines: list[Machine], apply: bool, report: Report) -> None:
    report.section("Plugins (user scope)")
    here = plugins(machines[0])
    wanted = {pid: p for pid, p in here.items() if p["enabled"]}
    for pid in sorted(set(here) - set(wanted)):
        report.note(f"{pid}: disabled on {machines[0].name}, so not synced")
    for host in machines[1:]:
        there = plugins(host)
        stale = []
        for pid, plugin in sorted(wanted.items()):
            have = there.get(pid)
            if have is None:
                report.drift(f"{host.name}: {pid} is not installed", apply)
                if apply:
                    market = pid.split("@", 1)[1]
                    if market not in marketplaces(host):
                        host.run(
                            [
                                "claude",
                                "plugin",
                                "marketplace",
                                "add",
                                marketplace_source(marketplaces(machines[0])[market]),
                            ]
                        )
                    host.run(["claude", "plugin", "install", pid])
                    report.fixed(f"{host.name}: installed {pid}")
                continue
            if not have["enabled"]:
                report.drift(f"{host.name}: {pid} is disabled", apply)
                if apply:
                    host.run(["claude", "plugin", "enable", pid])
                    report.fixed(f"{host.name}: enabled {pid}")
            if have["version"] != plugin["version"]:
                # Versions are often commit hashes, so neither side is known to be the newer one:
                # update both from their marketplace.
                report.drift(f"{pid}: {plugin['version']} here, {have['version']} on {host.name}", apply)
                stale.append(pid)
            else:
                report.ok(f"{host.name}: {pid} {have['version']}")
        for pid in sorted(set(there) - set(here)):
            report.note(f"{host.name}: {pid} is installed only there")
        if apply and stale:
            for m in (machines[0], host):
                for market in sorted({pid.split("@", 1)[1] for pid in stale}):
                    m.run(["claude", "plugin", "marketplace", "update", market])
                for pid in stale:
                    m.run(["claude", "plugin", "update", pid])
            now_here, now_there = plugins(machines[0]), plugins(host)
            for pid in stale:
                if now_here[pid]["version"] == now_there[pid]["version"]:
                    report.fixed(f"{pid}: both at {now_here[pid]['version']}")
                else:
                    report.problem(
                        f"{pid}: still {now_here[pid]['version']} here and {now_there[pid]['version']} on "
                        f"{host.name} after updating both: their marketplace catalogs disagree"
                    )
        if stale and apply:
            report.note("plugin updates apply from the next Claude Code session")


# --- CLAUDE.md ---


def split_claude_md(text: str, where: str) -> tuple[str, str]:
    """(shared part, machine's own part). The shared part ends with the marker line."""
    head, sep, tail = text.partition(MARKER + "\n")
    if not sep:
        raise RuntimeError(f"{where}: {CLAUDE_MD} has no line\n  {MARKER}\nput it above the machine's own section")
    return head + sep, tail


@dataclass
class Identity:
    """What a machine's generated CLAUDE.md section says about it."""

    hostname: str
    system: str  # `uname -sm`, e.g. "Darwin arm64"
    ssh: str | None  # how the source reaches it; None for the source itself


def identity(machine: Machine) -> Identity:
    return Identity(machine.out(["hostname"]), machine.out(["uname", "-sm"]), machine.host)


def machine_section(me: Identity, fleet: list[Identity]) -> str:
    """The generated section telling an agent which fleet machine it runs on. `fleet[0]` is the source."""
    source = fleet[0]
    if me.ssh is None:
        role = (
            f"This machine is `{me.hostname}` ({me.system}), the fleet's source of truth: `fleet` runs here "
            "and the other machines follow it."
        )
    else:
        role = (
            f"This machine is `{me.hostname}` ({me.system}), reached from `{source.hostname}` as `ssh {me.ssh}`. "
            f"It follows `{source.hostname}`: `fleet --apply` there overwrites the shared part of this file, "
            "`~/.claude/settings*.json`, the skill clones and the plugins here, so make those changes on "
            f"`{source.hostname}` (or tell me), not here."
        )
    members = "\n".join(
        f"- `{m.hostname}` ({m.system}): "
        + ("source of truth" if m.ssh is None else f"`ssh {m.ssh}`")
        + (" ← this one" if m == me else "")
        for m in fleet
    )
    return (
        f"{SECTION_START}\n"
        "### This machine\n\n"
        f"{role}\n\n"
        f"The fleet:\n\n{members}\n\n"
        "Memory saved here (`~/.claude/projects/<path-key>/memory/`) exists on this machine only: each machine "
        "has its own, and none of it is synced.\n"
        f"{SECTION_END}\n"
    )


def with_machine_section(own: str, section: str, where: str) -> str:
    """The machine's own part with the generated section replaced, or put first when it has none yet."""
    if SECTION_START not in own:
        return section + "\n" + own.lstrip("\n")
    before, _, rest = own.partition(SECTION_START + "\n")
    _, sep, after = rest.partition(SECTION_END + "\n")
    if not sep:
        raise RuntimeError(f"{where}: {CLAUDE_MD} has\n  {SECTION_START}\nbut no\n  {SECTION_END}\nbelow it")
    return before + section + after


def sync_claude_md(machines: list[Machine], apply: bool, report: Report) -> None:
    report.section(f"~/{CLAUDE_MD}")
    source = machines[0].read(CLAUDE_MD)
    if source is None:
        raise RuntimeError(f"{machines[0].name} has no ~/{CLAUDE_MD} to sync from")
    shared, _ = split_claude_md(source, machines[0].name)
    fleet = [identity(m) for m in machines]
    for machine, me in zip(machines, fleet, strict=True):
        text = source if machine is machines[0] else machine.read(CLAUDE_MD)
        if text is None:
            report.problem(f"{machine.name}: no ~/{CLAUDE_MD}; copy it, then write its own section below the marker")
            continue
        theirs, own = split_claude_md(text, machine.name)
        new_own = with_machine_section(own, machine_section(me, fleet), machine.name)
        if theirs == shared:
            report.ok(f"{machine.name}: shared part identical")
        else:
            report.drift(f"{machine.name}: shared part differs", apply)
            diff = difflib.unified_diff(
                theirs.splitlines(), shared.splitlines(), machine.name, machines[0].name, lineterm=""
            )
            for line in diff:
                report.note(line)
        if new_own == own:
            report.ok(f"{machine.name}: machine section current")
        else:
            report.drift(f"{machine.name}: machine section out of date", apply)
            for line in difflib.unified_diff(own.splitlines(), new_own.splitlines(), "now", "generated", lineterm=""):
                report.note(line)
        if apply and shared + new_own != text:
            backup = machine.write(CLAUDE_MD, shared + new_own)
            report.fixed(f"{machine.name}: updated ~/{CLAUDE_MD} (old file: ~/{backup})")


# --- shared AGENTS.md ---

CODEX_AGENTS_MD = ".codex/AGENTS.md"


def import_line(agents_md: str) -> str:
    return f"@~/{agents_md}"


def sync_agents_md(machines: list[Machine], config: Config, apply: bool, report: Report) -> None:
    """The file CLAUDE.md imports has to exist everywhere: a missing `@` import loads nothing, without a
    word, and every session runs without its rules. Codex has no imports: it reads one global file,
    `~/.codex/AGENTS.md`, which becomes a symlink to the same rules, and the machine's own part of
    CLAUDE.md goes into its `developer_instructions`."""
    path = config.agents_md
    if path is None:
        return
    report.section(f"~/{path}")
    source = machines[0].read(CLAUDE_MD)
    if source is None:
        raise RuntimeError(f"{machines[0].name} has no ~/{CLAUDE_MD} to import ~/{path} from")
    shared, _ = split_claude_md(source, machines[0].name)
    if import_line(path) not in shared.splitlines():
        report.problem(f"the shared part of ~/{CLAUDE_MD} doesn't import it: add the line `{import_line(path)}`")
    for machine in machines:
        if machine.read(path) is None:
            report.problem(
                f"{machine.name}: no ~/{path}, so its CLAUDE.md imports nothing. Pull the repo it lives in "
                "there (for a plugin marketplace, `claude plugin marketplace update <name>`)"
            )
        if sync_codex_link(machine, path, apply, report):
            sync_codex_instructions(machine, apply, report)


def sync_codex_link(machine: Machine, path: str, apply: bool, report: Report) -> bool:
    """Link Codex's global instructions to the shared rules. Returns whether Codex is installed."""
    home = machine.out(["sh", "-c", 'printf %s "$HOME"'])
    target = f"{home}/{path}"
    probe = (
        'if [ ! -d "$1" ]; then echo none; elif [ -L "$2" ]; then echo "link $(readlink "$2")"; '
        'elif [ -e "$2" ]; then echo file; else echo missing; fi'
    )
    state = machine.out(["sh", "-c", probe, "sh", f"{home}/.codex", f"{home}/{CODEX_AGENTS_MD}"])
    if state == "none":
        report.ok(f"{machine.name}: no Codex (~/.codex), nothing to link")
        return False
    if state == f"link {target}":
        report.ok(f"{machine.name}: ~/{CODEX_AGENTS_MD} links to it")
        return True
    report.drift(
        f"{machine.name}: ~/{CODEX_AGENTS_MD} is {state.replace('link ', 'a link to ')}, not a link to it", apply
    )
    if apply:
        backup = f"{home}/{CODEX_AGENTS_MD}.bak-{datetime.now().astimezone():%Y%m%d%H%M%S}"
        script = 'if [ -e "$1" ] && [ ! -L "$1" ]; then mv "$1" "$3"; fi; ln -sfn "$2" "$1"'
        machine.run(["sh", "-c", script, "sh", f"{home}/{CODEX_AGENTS_MD}", target, backup])
        kept = f" (old file: {backup})" if state == "file" else ""
        report.fixed(f"{machine.name}: ~/{CODEX_AGENTS_MD} now links to ~/{path}{kept}")
    return True


CODEX_CONFIG = ".codex/config.toml"
CODEX_BLOCK_START = (
    "# fleet: this machine's own instructions (its ~/.claude/CLAUDE.md below the marker), rewritten by `fleet --apply`"
)
CODEX_BLOCK_END = "# fleet: end of this machine's own instructions"
TOML_LITERAL = "'''"


def codex_instructions_block(own: str) -> str:
    """A top-level `developer_instructions` key holding the machine's own instructions, as a TOML
    multi-line literal string so nothing in the text needs escaping."""
    text = own.strip() + "\n"
    if TOML_LITERAL in text:
        raise RuntimeError(f"the machine's own part of CLAUDE.md contains {TOML_LITERAL}, which TOML can't hold here")
    return f"{CODEX_BLOCK_START}\ndeveloper_instructions = {TOML_LITERAL}\n{text}{TOML_LITERAL}\n{CODEX_BLOCK_END}\n"


def with_codex_instructions(config: str, own: str, where: str) -> str:
    """Codex's config.toml with fleet's block replaced, or put first: a top-level key has to come
    before the first table. Refuses a `developer_instructions` someone set by hand rather than
    overwrite it, and checks the result parses back to exactly this text."""
    block = codex_instructions_block(own)
    if CODEX_BLOCK_START in config:
        before, _, rest = config.partition(CODEX_BLOCK_START + "\n")
        _, sep, after = rest.partition(CODEX_BLOCK_END + "\n")
        if not sep:
            raise RuntimeError(f"{where}: ~/{CODEX_CONFIG} has\n  {CODEX_BLOCK_START}\nbut no\n  {CODEX_BLOCK_END}")
        new = before + block + after
    elif re.search(r"(?m)^\s*developer_instructions\s*=", config):
        raise RuntimeError(
            f"{where}: ~/{CODEX_CONFIG} sets developer_instructions by hand; move that text into the machine's "
            f"own part of ~/{CLAUDE_MD} and delete the key, so fleet can manage it"
        )
    else:
        new = block + "\n" + config
    if tomllib.loads(new).get("developer_instructions") != own.strip() + "\n":
        raise RuntimeError(f"{where}: rewriting ~/{CODEX_CONFIG} would not round-trip developer_instructions")
    return new


def sync_codex_instructions(machine: Machine, apply: bool, report: Report) -> None:
    claude_md = machine.read(CLAUDE_MD)
    config = machine.read(CODEX_CONFIG)
    if claude_md is None or config is None:
        missing = CLAUDE_MD if claude_md is None else CODEX_CONFIG
        report.problem(f"{machine.name}: has Codex but no ~/{missing}, so Codex gets no machine instructions")
        return
    _, own = split_claude_md(claude_md, machine.name)
    new = with_codex_instructions(config, own, machine.name)
    if new == config:
        report.ok(f"{machine.name}: Codex developer_instructions match its CLAUDE.md")
        return
    report.drift(f"{machine.name}: Codex developer_instructions differ from its CLAUDE.md's own part", apply)
    if apply:
        backup = machine.write(CODEX_CONFIG, new)
        report.fixed(f"{machine.name}: updated ~/{CODEX_CONFIG} (old file: ~/{backup})")


# --- settings ---


def flatten(settings: dict) -> dict[str, object]:
    """Top-level keys, and one level into object values: `env.X`, `hooks.Stop`, `enabledPlugins.Y`."""
    flat: dict[str, object] = {}
    for key, value in settings.items():
        if isinstance(value, dict):
            flat.update({f"{key}.{sub}": v for sub, v in value.items()})
        else:
            flat[key] = value
    return flat


def is_machine_key(key: str, machine_keys: list[str]) -> bool:
    return any(key == k or key.startswith(f"{k}.") for k in machine_keys)


def merge_settings(source: dict, target: dict, machine_keys: list[str]) -> dict:
    """`target` with every key copied from `source`, except the machine's own, which `target` keeps."""
    src, dst = flatten(source), flatten(target)
    keep = {k: v for k, v in dst.items() if is_machine_key(k, machine_keys)}
    take = {k: v for k, v in src.items() if not is_machine_key(k, machine_keys)}
    merged: dict = {}
    for key, value in {**take, **keep}.items():
        top, dot, sub = key.partition(".")
        if dot and (isinstance(source.get(top), dict) or isinstance(target.get(top), dict)):
            merged.setdefault(top, {})[sub] = value
        else:
            merged[key] = value
    # An object whose every member was dropped still exists on the source side: keep it, empty.
    for top, value in source.items():
        if isinstance(value, dict) and not is_machine_key(top, machine_keys):
            merged.setdefault(top, {})
    return merged


def sync_settings(machines: list[Machine], config: Config, apply: bool, report: Report) -> None:
    for path in SETTINGS_FILES:
        report.section(f"~/{path}")
        text = machines[0].read(path)
        if text is None:
            report.note(f"none on {machines[0].name}")
            continue
        source = json.loads(text)
        for host in machines[1:]:
            theirs_text = host.read(path)
            theirs = json.loads(theirs_text) if theirs_text is not None else {}
            merged = merge_settings(source, theirs, config.machine_settings)
            before, after = flatten(theirs), flatten(merged)
            changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
            if not changed:
                report.ok(f"{host.name}: shared keys identical")
                continue
            # Key names only: values include secrets.
            report.drift(f"{host.name}: {', '.join(changed)}", apply)
            if apply:
                if theirs_text is None:
                    host.run(["sh", "-c", 'mkdir -p "$(dirname "$1")" && touch "$1"', "sh", path])
                backup = host.write(path, json.dumps(merged, indent=2) + "\n")
                report.fixed(f"{host.name}: copied {len(changed)} key(s) (old file: ~/{backup})")


# --- reported only ---


def check_helpers(machines: list[Machine], config: Config, report: Report) -> None:
    report.section("Helper commands")
    for m in machines:
        # Through a login shell: Claude Code runs hooks with its PATH (~/.local/bin), not ssh's bare one.
        probe = ["sh", "-c", 'exec "$SHELL" -lc \'command -v "$1"\' sh "$1"', "sh"]
        missing = [h for h in config.helpers if m.run([*probe, h], check=False).returncode]
        if missing:
            report.problem(
                f"{m.name}: missing {', '.join(missing)}: a hook or the statusline that calls one fails silently"
            )
        else:
            report.ok(f"{m.name}: {', '.join(config.helpers)}")


def check_t3(machines: list[Machine], report: Report) -> None:
    report.section("T3 Code")
    app = machines[0].run(["sh", "-c", '"$HOME/.t3/bin/t3" --version'], check=False).stdout.strip().removeprefix("t3 v")
    if not app:
        report.note(f"no T3 Code on {machines[0].name}")
        return
    for host in machines[1:]:
        served = host.run(["sh", "-c", "ps -eo args | grep -o '[.]t3/runtime/versions/[^/]*/t3 serve'"], check=False)
        versions = {line.split("/")[3] for line in served.stdout.splitlines()}
        if not versions:
            report.note(f"{host.name}: no T3 server running; the app installs {app} there when it connects")
        elif versions == {app}:
            report.ok(f"{host.name}: serving {app}, the app's own version")
        else:
            report.problem(
                f"{host.name}: serving {', '.join(sorted(versions))}, app is {app}: reconnect the environment in T3 Code"
            )


def sync(apply: bool) -> int:
    config = load_config()
    machines = [Machine(None), *(Machine(h) for h in config.hosts)]
    report = Report()
    sync_claude_code(machines, apply, report)
    sync_skills(machines, config, apply, report)
    sync_plugins(machines, apply, report)
    sync_claude_md(machines, apply, report)
    sync_agents_md(machines, config, apply, report)
    sync_settings(machines, config, apply, report)
    check_helpers(machines, config, report)
    check_t3(machines, report)
    print()
    if report.unresolved:
        hint = "" if apply else " (`fleet --apply` fixes the DRIFT lines)"
        print(f"{report.unresolved} item(s) out of sync{hint}")
        return 1
    print("The fleet is in sync.")
    return 0
