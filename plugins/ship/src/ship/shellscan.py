"""Reading the commands out of a shell command line, far enough for the merge guard to find each one.

Not a shell parser: it finds the simple commands a line runs, which is what the guard needs to
spot `gh pr merge` however an agent wraps it. Quotes are resolved, so a quoted mention is an
argument, not a command. Separators (`;`, `&&`, `||`, `|`, `&`, newlines, parentheses) split
commands. A heredoc's body is data, except for the `$(…)` and backticks an unquoted one expands.
Command substitutions run too, so their commands come out, just before the command they sit in.
What goes in front of a command without changing it goes: reserved words (`if`, `then`, `do`, `{`,
`!`, …), the wrappers `command`, `builtin`, `exec`, `env`, `sudo`, `nohup` and `time`, and leading
`NAME=value` assignments, which are kept as the command's environment.

Raises ValueError for a line it can't read (an unclosed quote), so the caller decides what an
unreadable line means.
"""

import re
import shlex
from dataclasses import dataclass, field

_SEPARATORS = set(";&|()\n")
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
_PLACEHOLDER = re.compile(r"__ship_substitution_(\d+)__")
_HEREDOC = re.compile(r"(?<!<)<<(-?)(?!<)\s*(['\"]?)([A-Za-z_]\w*)\2")
_RESERVED = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "{", "}", "!", "time"}
_WRAPPERS = {"command", "builtin", "exec", "nohup", "env", "sudo"}
# Flags of `sudo` and `env` that take a value, so the value isn't read as the command.
_WRAPPER_VALUE_FLAGS = {"-u", "-g", "-C", "-h", "-p", "-U", "-S", "--chdir", "--unset"}


@dataclass(frozen=True)
class Command:
    words: list[str]
    env: dict[str, str] = field(default_factory=dict)


def _cut_heredocs(text: str) -> tuple[str, list[str]]:
    """`text` without its heredoc bodies, and the bodies that expand substitutions (unquoted delimiter)."""
    kept: list[str] = []
    expanding: list[str] = []
    lines = iter(text.split("\n"))
    for line in lines:
        kept.append(line)
        for heredoc in _HEREDOC.finditer(line):
            strip_tabs, quote, delimiter = heredoc.groups()
            body = []
            for body_line in lines:
                if (body_line.lstrip("\t") if strip_tabs else body_line) == delimiter:
                    break
                body.append(body_line)
            if not quote:
                expanding.append("\n".join(body))
    return "\n".join(kept), expanding


def _lift_substitutions(text: str, inner: list[str]) -> str:
    """`text` with each `$(…)` and backtick substitution, innermost first, swapped for a placeholder
    word naming its text in `inner`."""

    def lift(found: re.Match) -> str:
        inner.append(found.group(1) if found.group(1) is not None else found.group(2))
        return f"__ship_substitution_{len(inner) - 1}__"

    while True:
        text, count = _SUBSTITUTION.subn(lift, text)
        if not count:
            return text


def _words(text: str) -> list[list[str]]:
    lex = shlex.shlex(text, posix=True, punctuation_chars=";&|()\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    segments: list[list[str]] = [[]]
    for token in lex:
        if set(token) <= _SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [s for s in segments if s]


def _command(words: list[str]) -> Command | None:
    env: dict[str, str] = {}
    words = list(words)
    while words:
        head = words[0]
        if head in _RESERVED:
            words.pop(0)
        elif head in _WRAPPERS:
            words.pop(0)
            while words and words[0].startswith("-"):
                flag = words.pop(0)
                if flag in _WRAPPER_VALUE_FLAGS and words:
                    words.pop(0)
        elif "=" in head and head.split("=", 1)[0].isidentifier():
            name, _, value = words.pop(0).partition("=")
            env[name] = value
        else:
            return Command(words=words, env=env)
    return None


def _commands(text: str, inner: list[str]) -> list[Command]:
    text, bodies = _cut_heredocs(text)
    found: list[Command] = []
    for body in bodies:
        for substitution in _SUBSTITUTION.finditer(body):
            found += commands(substitution.group(1) if substitution.group(1) is not None else substitution.group(2))
    for words in _words(_lift_substitutions(text, inner)):
        for word in words:
            for placeholder in _PLACEHOLDER.finditer(word):
                found += _commands(inner[int(placeholder.group(1))], inner)
        if (command := _command(words)) is not None:
            found.append(command)
    return found


def commands(text: str) -> list[Command]:
    """The simple commands `text` runs, in order (a substitution's before the command holding it)."""
    return _commands(text, [])
