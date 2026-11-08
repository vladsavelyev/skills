"""Subprocess helpers. Every call is a fixed argv built from developer input, never shell=True."""

import subprocess
from pathlib import Path


class ToolMissing(RuntimeError):
    """A required external tool (git, ssh, claude) is not on PATH."""


def run(
    argv: list[str], cwd: Path | None = None, check: bool = True, input: str | None = None
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, cwd=cwd, check=check, text=True, capture_output=True, input=input)
    except FileNotFoundError as exc:
        raise ToolMissing(f"{argv[0]} is not on PATH") from exc


def out(argv: list[str], cwd: Path | None = None) -> str:
    """stdout of a command that must succeed."""
    return run(argv, cwd=cwd).stdout.strip()


def try_out(argv: list[str], cwd: Path | None = None) -> str:
    """stdout, or "" if the command fails or is missing. For probes whose absence is meaningful."""
    try:
        proc = run(argv, cwd=cwd, check=False)
    except ToolMissing:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""
