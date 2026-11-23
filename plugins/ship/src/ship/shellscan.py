"""Reading a shell command line into the commands it runs, far enough for the merge guard.

Not a full shell parser: it reads the subset agents write. Words, with quotes and escapes resolved
the way bash does (`$(…)` and backticks expand outside single quotes, so a quoted commit message
is an argument, not a command). Separators (`;`, `&&`, `||`, `|`, `&`, newlines). Scopes: a `( … )`
subshell and a command substitution are each their own, so a `cd` inside one stays there; `{ … }`
and reserved words are plain words, in the same scope. Heredocs: the body is data, but an unquoted
one expands its substitutions.

The result is a tree (`Script` of `Command`s and nested `Script`s), so the guard knows which `cd`
applies to which command. Raises ValueError for a line it can't read (an unclosed quote or paren),
so the caller decides what an unreadable line means.
"""

from dataclasses import dataclass, field

_SPACE = " \t\r"
_OPERATORS = ";&|"


@dataclass
class Command:
    """One simple command: its words, and the scripts its substitutions and heredocs run first."""

    words: list[str] = field(default_factory=list)
    substitutions: list["Script"] = field(default_factory=list)


@dataclass
class Script:
    """A sequence of commands run in one scope; a nested `Script` is a subshell."""

    items: list["Command | Script"] = field(default_factory=list)


class _Reader:
    def __init__(self, text: str):
        self.text = text
        self.i = 0
        self.heredocs: list[tuple[str, bool, bool, Command]] = []  # delimiter, quoted, strip tabs, command

    def peek(self, n: int = 1) -> str:
        return self.text[self.i : self.i + n]

    def script(self, closer: str | None) -> Script:
        """Read commands up to `closer` (`)` for a subshell or substitution) or the end."""
        script = Script()
        command = Command()

        def finish():
            nonlocal command
            if command.words or command.substitutions:
                script.items.append(command)
            command = Command()

        while self.i < len(self.text):
            c = self.peek()
            if c in _SPACE:
                self.i += 1
            elif c == "\\" and self.peek(2) == "\\\n":
                self.i += 2
            elif c == "\n":
                self.i += 1
                finish()
                self._heredoc_bodies()
            elif c in _OPERATORS:
                self.i += 1
                finish()
            elif c == "#":
                while self.i < len(self.text) and self.peek() != "\n":
                    self.i += 1
            elif c == "(":
                self.i += 1
                finish()
                script.items.append(self.script(")"))
            elif c == ")":
                if closer != ")":
                    raise ValueError("unmatched )")
                self.i += 1
                finish()
                return script
            elif self.peek(2) == "<<" and self.peek(3) != "<<<":
                self._heredoc_start(command)
            else:
                command.words.append(self._word(command))
        if closer is not None:
            raise ValueError(f"missing {closer}")
        finish()
        return script

    def _word(self, command: Command) -> str:
        out: list[str] = []
        while self.i < len(self.text):
            c = self.peek()
            if c in _SPACE or c == "\n" or c in "()" or (c in _OPERATORS and not self._redirect_ampersand(out)):
                break
            if c == "\\":
                out.append(self.text[self.i + 1 : self.i + 2])
                self.i += 2
            elif c == "'":
                end = self.text.find("'", self.i + 1)
                if end < 0:
                    raise ValueError("No closing quotation")
                out.append(self.text[self.i + 1 : end])
                self.i = end + 1
            elif c == '"':
                self.i += 1
                out.append(self._double_quoted(command))
            elif self.peek(2) == "$(":
                self.i += 2
                command.substitutions.append(self.script(")"))
                out.append("$(…)")
            elif c == "`":
                command.substitutions.append(self._backticks())
                out.append("`…`")
            else:
                out.append(c)
                self.i += 1
        return "".join(out)

    def _redirect_ampersand(self, out: list[str]) -> bool:
        # `2>&1` and `>&2` are one word, not a background `&`.
        return self.peek() == "&" and bool(out) and out[-1].endswith((">", "<"))

    def _double_quoted(self, command: Command, *, closing: str | None = '"') -> str:
        """Read a double-quoted word's text after its opening quote, through `closing`; with `closing`
        None, to the end (a heredoc body, which expands the same way but has no closing quote)."""
        out: list[str] = []
        while True:
            if self.i >= len(self.text):
                if closing is None:
                    return "".join(out)
                raise ValueError("No closing quotation")
            c = self.peek()
            if c == closing:
                self.i += 1
                return "".join(out)
            if c == "\\" and self.peek(2)[1:] in ('"', "\\", "$", "`", "\n"):
                out.append(self.text[self.i + 1])
                self.i += 2
            elif self.peek(2) == "$(":
                self.i += 2
                command.substitutions.append(self.script(")"))
                out.append("$(…)")
            elif c == "`":
                command.substitutions.append(self._backticks())
                out.append("`…`")
            else:
                out.append(c)
                self.i += 1

    def _backticks(self) -> Script:
        end = self.text.find("`", self.i + 1)
        if end < 0:
            raise ValueError("No closing backtick")
        inner = self.text[self.i + 1 : end]
        self.i = end + 1
        return parse(inner)

    def _heredoc_start(self, command: Command) -> None:
        self.i += 2
        strip_tabs = self.peek() == "-"
        self.i += strip_tabs
        while self.peek() in (" ", "\t"):
            self.i += 1
        start = self.i
        delimiter = self._word(Command())
        quoted = any(q in self.text[start : self.i] for q in "'\"\\")
        self.heredocs.append((delimiter, quoted, strip_tabs, command))

    def _heredoc_bodies(self) -> None:
        """At a newline: read the bodies of the heredocs its line started."""
        for delimiter, quoted, strip_tabs, command in self.heredocs:
            body: list[str] = []
            while self.i < len(self.text):
                end = self.text.find("\n", self.i)
                end = len(self.text) if end < 0 else end
                line = self.text[self.i : end]
                self.i = min(end + 1, len(self.text))
                if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                    break
                body.append(line)
            if not quoted:
                command.substitutions += _expansions("\n".join(body))
        self.heredocs = []


def _expansions(text: str) -> list[Script]:
    """The substitutions an unquoted heredoc body runs: it expands like a double-quoted word."""
    command = Command()
    _Reader(text)._double_quoted(command, closing=None)
    return command.substitutions


def parse(text: str) -> Script:
    """The commands `text` runs. Raises ValueError for a line it can't read."""
    reader = _Reader(text)
    script = reader.script(None)
    reader._heredoc_bodies()
    return script
