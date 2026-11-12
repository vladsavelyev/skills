"""Mirrors: other files on each machine holding a copy of a settings value, which `fleet set` updates with it.

A secret in `~/.claude/settings.json` can have copies elsewhere: Codex reads the same gateway key from
its own `~/.codex/config.toml`. The fleet config lists them:

    [[mirrors]]
    setting = "env.AWS_BEARER_TOKEN_BEDROCK"
    file = ".codex/config.toml"
    key = "model_providers.azure.http_headers.api-key"

Only TOML, and only a string that is already there: the stdlib reads TOML but can't write it, so the old
value's string literal is replaced in the text, and the result is parsed back to check that this key, and
nothing else, changed. Nothing here may put a value in a message.
"""

import json
import tomllib
from dataclasses import dataclass


@dataclass(frozen=True)
class Mirror:
    setting: str  # the settings key it copies, e.g. env.AWS_BEARER_TOKEN_BEDROCK
    file: str  # relative to the home directory
    key: str  # dotted path in the file

    def __post_init__(self) -> None:
        if not self.file.endswith(".toml"):
            raise RuntimeError(f"mirror ~/{self.file}: only TOML files can mirror a setting")

    def __str__(self) -> str:
        return f"~/{self.file} {self.key}"


def value_in(text: str, key: str) -> object | None:
    """The value at a dotted key path in TOML text, or None when the path isn't there."""
    node: object = tomllib.loads(text)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def with_value(text: str, key: str, value: str, where: str) -> str:
    """TOML text with the string at `key` replaced by `value`, everything else byte for byte as it was."""
    old = value_in(text, key)
    match old:
        case str():
            pass
        case None:
            raise RuntimeError(f"{where}: {key} is not there; `fleet set` only replaces a string that is")
        case _:
            raise RuntimeError(
                f"{where}: {key} is of type {type(old).__name__}, not a string; `fleet set` only replaces strings"
            )
    if old == value:
        return text
    literals = {f: text.count(f) for f in (json.dumps(old), f"'{old}'") if f in text}
    if sum(literals.values()) != 1:
        raise RuntimeError(
            f"{where}: the current value of {key} is written {sum(literals.values())} times, not once: update it by hand"
        )
    [literal] = literals
    new = text.replace(literal, json.dumps(value))
    expected = tomllib.loads(text)
    node = expected
    *parents, last = key.split(".")
    for part in parents:
        node = node[part]
    node[last] = value
    if tomllib.loads(new) != expected:
        raise RuntimeError(f"{where}: replacing {key} in the text would change more than {key}: update it by hand")
    return new
