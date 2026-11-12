"""`fleet set <key>`: give one `~/.claude/settings.json` key a new value on every machine, e.g. a rotated secret.

The value comes from stdin, or a prompt that doesn't echo it, and is never printed: the report shows
key names and a sha256 prefix only. It's validated first when the key has a validator (VALIDATORS),
written here with a `.bak-<timestamp>` copy of the old file, pushed to the hosts through the
settings sync with only that key selected, and read back from every machine to compare hashes. The
key's mirrors (copies in other files, e.g. Codex's config: mirrors.py) get the value on every machine
too, and are checked the same way.
"""

import getpass
import hashlib
import json
import sys
from collections.abc import Callable
from functools import partial

from . import mirrors, validators
from .mirrors import Mirror
from .sync import (
    SETTINGS_JSON,
    Config,
    Machine,
    Report,
    flatten,
    is_machine_key,
    kept,
    load_config,
    settings_json,
    sync_settings_file,
)


def run(key: str) -> int:
    """The CLI: refuse a bad key before asking for its value."""
    config = load_config()
    check_key(key, config)
    value = read_value(key)
    machines = [Machine(None), *(Machine(h) for h in config.hosts)]
    return set_key(machines, key, value, [m for m in config.mirrors if m.setting == key])


def check_key(key: str, config: Config) -> None:
    top, dot, sub = key.partition(".")
    if not top or (dot and not sub):
        raise RuntimeError(f"`{key}` is not a settings key path: give `name` or `object.name`, e.g. `env.MY_TOKEN`")
    if is_machine_key(key, config.machine_settings):
        raise RuntimeError(f"`{key}` is each machine's own (machine_settings in the fleet config): set it on each one")


def read_value(key: str) -> str:
    """From stdin when it isn't a terminal (`fleet set KEY < file`), else from a prompt that doesn't echo."""
    if sys.stdin.isatty():
        value = getpass.getpass(f"New value for {key} (not echoed): ")
    else:
        value = sys.stdin.read().removesuffix("\n").removesuffix("\r")
    if not value:
        raise RuntimeError(f"no value given for {key}")
    if "\n" in value or "\r" in value:
        raise RuntimeError(f"the value given for {key} spans several lines: give it on one")
    return value


def with_value(settings: dict, key: str, value: str) -> dict:
    """A copy of `settings` with `key` (a flattened name, as `fleet` syncs them) set to `value`."""
    new = json.loads(json.dumps(settings))
    top, dot, sub = key.partition(".")
    name = sub if dot else top
    match new.setdefault(top, {}) if dot else new:
        case dict() as parent if isinstance(parent.get(name, ""), str):
            parent[name] = value
            return new
        case dict() as parent:
            what = type(parent[name]).__name__
            raise RuntimeError(
                f"`{key}` in ~/{SETTINGS_JSON} is of type {what}, not a string: `fleet set` only replaces strings"
            )
        case _:
            raise RuntimeError(f"`{top}` in ~/{SETTINGS_JSON} is not an object, so it has no `{sub}`")


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12]


def read_settings(machine: Machine) -> dict:
    text = machine.read(SETTINGS_JSON)
    if text is None:
        raise RuntimeError(f"{machine.name} has no ~/{SETTINGS_JSON}")
    return json.loads(text)


def set_mirror(machine: Machine, mirror: Mirror, value: str, report: Report) -> None:
    text = machine.read(mirror.file)
    if text is None:
        report.note(f"{machine.name}: no ~/{mirror.file}, so no {mirror.key} to update")
        return
    new = mirrors.with_value(text, mirror.key, value, f"{machine.name}: ~/{mirror.file}")
    if new == text:
        report.ok(f"{machine.name}: {mirror} identical")
        return
    backup = machine.write(mirror.file, new)
    report.fixed(f"{machine.name}: set {mirror}{kept(backup)}")


class Rollout:
    """One value going to every machine: what failed where, and the hash it's checked by."""

    def __init__(self, value: str):
        self.value = value
        self.want = fingerprint(value)
        self.report = Report()
        self.failed: set[str] = set()

    def fail(self, machine: Machine, text: str) -> None:
        self.failed.add(machine.name)
        # A Machine's own errors already start with its name.
        self.report.failed(f"{machine.name}: {text.removeprefix(f'{machine.name}: ')}")

    def attempt(self, machine: Machine, step: Callable[[], None]) -> None:
        """Run one machine's step; a failure is that machine's, and the others still get theirs."""
        try:
            step()
        except (RuntimeError, ValueError) as exc:
            self.fail(machine, str(exc))

    def check(self, machine: Machine, label: str, there: object) -> None:
        if there == self.value:
            self.report.ok(f"{machine.name}: {label}")
        elif there is None:
            self.fail(machine, f"has no {label}")
        else:
            got = fingerprint(there) if isinstance(there, str) else type(there).__name__
            self.fail(machine, f"{label} has sha256 {got}…, not {self.want}…")

    def verify(self, machine: Machine, key: str, copies: list[Mirror]) -> None:
        self.check(machine, key, flatten(read_settings(machine)).get(key))
        for mirror in copies:
            text = machine.read(mirror.file)
            if text is None:
                self.report.note(f"{machine.name}: no ~/{mirror.file}")
            else:
                self.check(machine, str(mirror), mirrors.value_in(text, mirror.key))


def set_key(machines: list[Machine], key: str, value: str, copies: list[Mirror]) -> int:
    """`machines[0]` is this machine, the source; the rest are the hosts. `copies` are the key's mirrors."""
    here = machines[0]
    rollout = Rollout(value)
    report = rollout.report
    settings = read_settings(here)
    updated = with_value(settings, key, value)

    report.section(f"Validate {key}")
    validator = validators.VALIDATORS.get(key)
    if validator is None:
        report.note(f"no validator for {key}: it is written without being validated")
    else:
        validator(value, settings)
        report.ok(f"{key}: accepted by {validator.__name__}")

    report.section(f"Write {key}" + "".join(f", {m}" for m in copies))
    if updated == settings:
        report.ok(f"{here.name}: {key} already set to this value")
    else:
        backup = here.write(SETTINGS_JSON, settings_json(updated))
        report.fixed(f"{here.name}: set {key}{kept(backup)}")
    for machine in machines:
        if machine is not here:
            rollout.attempt(
                machine,
                partial(sync_settings_file, machine, SETTINGS_JSON, updated, lambda k: k == key, key, True, report),
            )
        for mirror in copies:
            rollout.attempt(machine, partial(set_mirror, machine, mirror, value, report))

    report.section(f"Verify: sha256 {rollout.want}… on every machine")
    for machine in machines:
        rollout.attempt(machine, partial(rollout.verify, machine, key, copies))

    print()
    if rollout.failed:
        print(f"Failed on {', '.join(sorted(rollout.failed))}: run `fleet set {key}` again once fixed")
        return 1
    print(f"{key} is set on every machine.")
    return 0
