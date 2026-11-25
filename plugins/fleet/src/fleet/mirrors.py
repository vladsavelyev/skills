"""Mirrors: other places on each machine holding a copy of a settings value, which `fleet set` updates with it.

A secret in `~/.claude/settings.json` can have copies elsewhere: Codex reads the same gateway key from
its own `~/.codex/config.toml`, and a T3 Code provider keeps it in T3's secret store. The fleet config
lists them:

    [[mirrors]]  # a string in a TOML or JSON file
    setting = "env.AWS_BEARER_TOKEN_BEDROCK"
    file = ".codex/config.toml"
    key = "model_providers.azure.http_headers.api-key"

    [[mirrors]]  # a sensitive environment variable of a T3 Code provider instance
    setting = "env.AWS_BEARER_TOKEN_BEDROCK"
    t3_provider = "antigravity"
    variable = "AGY_LLM_GATEWAY_API_KEY"

A file mirror replaces only a string that is already there. TOML is edited as text (the stdlib reads
TOML but can't write it): the old value's literal is replaced and the result is parsed back to check
that this key, and nothing else, changed. JSON is parsed and written back whole. Nothing here may put a
value in a message.
"""

import base64
import json
import tomllib
from dataclasses import dataclass

# T3 Code has no API for a provider's environment, so a T3 mirror writes its server's files directly.
# This couples `fleet` to T3's on-disk layout on purpose (checked against T3 Code 0.1.12): a sensitive
# provider variable is a raw-bytes file in the secret store, named for the instance and the variable,
# and the server re-reads it when its settings file is replaced, restarting that provider with the
# new value. `fleet set` only replaces a secret file T3 already wrote, so if T3 renames them the mirror
# fails instead of `fleet` writing a file nothing reads and reporting it ok.
T3_SETTINGS = ".t3/userdata/settings.json"
T3_SECRETS = ".t3/userdata/secrets"


@dataclass(frozen=True)
class FileMirror:
    setting: str  # the settings key it copies, e.g. env.AWS_BEARER_TOKEN_BEDROCK
    file: str  # relative to the home directory
    key: str  # dotted path in the file

    def __post_init__(self) -> None:
        if not self.file.endswith((".toml", ".json")):
            raise RuntimeError(f"mirror ~/{self.file}: only a TOML or JSON file can mirror a setting")

    def __str__(self) -> str:
        return f"~/{self.file} {self.key}"

    def value_in(self, text: str) -> object | None:
        """The value at `key` in the file's text, or None when the path isn't there."""
        return value_at(self.parse(text), self.key)

    def with_value(self, text: str, value: str, where: str) -> str:
        """The file's text with the string at `key` replaced by `value`."""
        old = self.value_in(text)
        match old:
            case str():
                pass
            case None:
                raise RuntimeError(f"{where}: {self.key} is not there; `fleet set` only replaces a string that is")
            case _:
                raise RuntimeError(
                    f"{where}: {self.key} is of type {type(old).__name__}, not a string; `fleet set` only replaces strings"
                )
        if old == value:
            return text
        if self.file.endswith(".toml"):
            return toml_with_value(text, self.key, old, value, where)
        data = self.parse(text)
        *parents, last = self.key.split(".")
        node = data
        for part in parents:
            node = node[part]
        node[last] = value
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    def parse(self, text: str) -> object:
        return tomllib.loads(text) if self.file.endswith(".toml") else json.loads(text)


@dataclass(frozen=True)
class T3Mirror:
    setting: str  # the settings key it copies
    t3_provider: str  # the provider instance id in T3's settings, e.g. antigravity
    variable: str  # the instance's environment variable holding the copy

    def __str__(self) -> str:
        return f"T3 provider {self.t3_provider} {self.variable}"

    @property
    def secret(self) -> str:
        """The secret store file holding the value, relative to the home directory."""
        return f"{T3_SECRETS}/provider-env-{b64(self.t3_provider)}-{b64(self.variable)}.bin"

    def configured(self, settings_text: str, where: str) -> bool:
        """Whether T3's settings give the instance this variable, kept in the secret store. A variable stored
        as plain text in the settings file is refused: replacing the secret file would not change it."""
        settings = json.loads(settings_text)
        instance = settings.get("providerInstances", {}).get(self.t3_provider)
        if instance is None:
            return False
        entries = [e for e in instance.get("environment", []) if e.get("name") == self.variable]
        if not entries:
            return False
        if not all(e.get("sensitive") and e.get("valueRedacted") for e in entries):
            raise RuntimeError(
                f"{where}: {self} is not kept in T3's secret store: mark it sensitive in T3's provider settings"
            )
        return True


Mirror = FileMirror | T3Mirror


def parse(entry: dict) -> Mirror:
    """A `[[mirrors]]` entry of the fleet config."""
    return T3Mirror(**entry) if "t3_provider" in entry else FileMirror(**entry)


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def value_at(node: object, key: str) -> object | None:
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def basic_string(value: str) -> str:
    """A TOML basic string. JSON's escapes are TOML's, but for characters outside the BMP (JSON's surrogate
    pairs aren't valid TOML, so they stay as they are) and DEL (raw in JSON, an escape in TOML)."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def toml_with_value(text: str, key: str, old: str, value: str, where: str) -> str:
    """TOML text with the string `old` at `key` replaced by `value`, everything else byte for byte as it was."""
    literals = {f: text.count(f) for f in (basic_string(old), f"'{old}'") if f in text}
    if sum(literals.values()) != 1:
        raise RuntimeError(
            f"{where}: the current value of {key} is written {sum(literals.values())} times, not once: update it by hand"
        )
    [literal] = literals
    new = text.replace(literal, basic_string(value))
    expected = tomllib.loads(text)
    node = expected
    *parents, last = key.split(".")
    for part in parents:
        node = node[part]
    node[last] = value
    if tomllib.loads(new) != expected:
        raise RuntimeError(f"{where}: replacing {key} in the text would change more than {key}: update it by hand")
    return new
