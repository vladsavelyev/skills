"""`fleet set <key>`: give one `~/.claude/settings.json` key a new value on every machine, e.g. a rotated secret.

The value comes from stdin, or a prompt that doesn't echo it, and is never printed: the report shows
key names and a sha256 prefix only. It's validated first when the key has a validator (VALIDATORS),
written here with a `.bak-<timestamp>` copy of the old file, pushed to the hosts through the
settings sync with only that key selected, and read back from every machine to compare hashes.
"""

import getpass
import hashlib
import json
import sys

from . import validators
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
    return set_key([Machine(None), *(Machine(h) for h in config.hosts)], key, value)


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
                f"`{key}` in ~/{SETTINGS_JSON} is a {what}, not a string: `fleet set` only replaces strings"
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


def set_key(machines: list[Machine], key: str, value: str) -> int:
    """`machines[0]` is this machine, the source; the rest are the hosts."""
    here = machines[0]
    report = Report()
    failed: set[str] = set()

    def fail(machine: Machine, text: str) -> None:
        failed.add(machine.name)
        # A Machine's own errors already start with its name.
        report.failed(f"{machine.name}: {text.removeprefix(f'{machine.name}: ')}")

    settings = read_settings(here)
    updated = with_value(settings, key, value)

    report.section(f"Validate {key}")
    validator = validators.VALIDATORS.get(key)
    if validator is None:
        report.note(f"no validator for {key}: it is written without being validated")
    else:
        validator(value, settings)
        report.ok(f"{key}: accepted by {validator.__name__}")

    report.section(f"Write {key} to ~/{SETTINGS_JSON}")
    if updated == settings:
        report.ok(f"{here.name}: already set to this value")
    else:
        backup = here.write(SETTINGS_JSON, settings_json(updated))
        report.fixed(f"{here.name}: set {key}{kept(backup)}")
    for host in machines[1:]:
        try:
            sync_settings_file(host, SETTINGS_JSON, updated, lambda k: k == key, key, True, report)
        except (RuntimeError, ValueError) as exc:
            fail(host, str(exc))

    want = fingerprint(value)
    report.section(f"Verify: {key} has sha256 {want}… on every machine")
    for machine in machines:
        try:
            there = flatten(read_settings(machine)).get(key)
        except (RuntimeError, ValueError) as exc:
            fail(machine, str(exc))
            continue
        if there == value:
            report.ok(machine.name)
        elif there is None:
            fail(machine, f"has no {key}")
        else:
            got = fingerprint(there) if isinstance(there, str) else type(there).__name__
            fail(machine, f"{key} has sha256 {got}…, not {want}…")

    print()
    if failed:
        print(f"Failed on {', '.join(sorted(failed))}: run `fleet set {key}` again once fixed")
        return 1
    print(f"{key} is set on every machine.")
    return 0
