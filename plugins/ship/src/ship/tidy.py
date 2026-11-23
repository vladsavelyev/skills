"""Removing a closed-out feature's checkout and branch, once they hold nothing the PR doesn't.

What a checkout holds beyond its commits is either work, which removing it would delete without a word, or what
running the project made there. Work blocks the removal: a modified or untracked file, a commit the PR doesn't have,
a file hidden from `status` by assume-unchanged or skip-worktree, an initialized submodule. An ignored path is what
the project made when the main checkout has it too (`.env`, a local config, logs: every checkout that runs the
project carries them), or when it is a cache any checkout rebuilds (`.venv`, `node_modules`, `__pycache__`). Any
other ignored path, eval results say, may be the user's, so it blocks until they accept it.

Every folder must also be one we can empty: a container that ran as root leaves root-owned files on a Linux host,
and `git worktree remove` unregisters the checkout before it fails on them, leaving the rest on disk. Those are
found first, and nothing is removed while any remain.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from .shell import run, try_run

# Folder names whose contents any checkout rebuilds: environments, dependency trees, bytecode, tool caches.
REBUILDABLE = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".cache",
    }
)


@dataclass
class Check:
    """What stops a checkout from being removed. Empty lists mean it holds nothing the PR doesn't."""

    changed: list[str] = field(default_factory=list)
    unpushed: str | None = None
    hidden: list[str] = field(default_factory=list)
    submodules: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    undeletable: list[str] = field(default_factory=list)

    @property
    def clear(self) -> bool:
        return not (
            self.changed or self.unpushed or self.hidden or self.submodules or self.unexpected or self.undeletable
        )

    def __str__(self) -> str:
        lines = []
        if self.unpushed:
            lines.append(self.unpushed)
        for title, paths in (
            ("modified or untracked (commit and push them, or delete them)", self.changed),
            ("hidden from `git status` by assume-unchanged or skip-worktree", self.hidden),
            ("initialized submodules, which `git worktree remove` refuses", self.submodules),
            (
                "ignored, but neither in the main checkout nor a rebuildable cache: pass --accept <path> for each the user OKs deleting",
                self.unexpected,
            ),
            (
                "not removable by you (made by a container running as root?): fix where they are made, so no sudo is needed",
                self.undeletable,
            ),
        ):
            if paths:
                lines.append(f"{title}:")
                lines.extend(f"  {path}" for path in paths)
        return "\n".join(lines)


def _status(checkout: str, *flags: str) -> list[tuple[str, str]]:
    """`git status` entries as (kind, path), kind one of changed, untracked, ignored.

    Porcelain v2 with NUL separators: no path is quoted or split, and no record starts with a space for the stripped
    output to eat (v1's " M" would lose it).
    """
    out = run(["git", "-C", checkout, "status", "--porcelain=v2", "-z", *flags])
    entries, records = [], iter(out.split("\0"))
    for record in records:
        match record[:1]:
            case "1":
                entries.append(("changed", record.split(" ", 8)[8]))
            case "2":
                entries.append(("changed", record.split(" ", 9)[9]))
                next(records)  # a rename's source path follows its target
            case "u":
                entries.append(("changed", record.split(" ", 10)[10]))
            case "?":
                entries.append(("untracked", record[2:]))
            case "!":
                entries.append(("ignored", record[2:]))
    return entries


def rebuildable(path: str) -> bool:
    """Whether an ignored path lies in a cache any checkout rebuilds."""
    parts = Path(path.rstrip("/")).parts
    return any(part in REBUILDABLE or part.endswith(".egg-info") for part in parts)


def unexpected_ignored(checkout: str, main: str, accepted: set[str]) -> list[str]:
    """Ignored paths that running the project doesn't explain: not in the main checkout, not a rebuildable cache."""
    # `normal`, not `all`: an ignored folder is one entry, so `logs/` is matched as a whole against the main checkout.
    entries = _status(checkout, "--ignored", "--untracked-files=normal")
    ignored = [path for kind, path in entries if kind == "ignored"]
    return [
        path
        for path in ignored
        if not rebuildable(path) and not (Path(main) / path).exists() and path.rstrip("/") not in accepted
    ]


def undeletable(checkout: str) -> list[str]:
    """Folders under `checkout` we can't empty: a file in a folder is deleted with write access to that folder."""
    stuck = []
    for top, dirs, files in os.walk(checkout):
        if (dirs or files) and not os.access(top, os.W_OK | os.X_OK):
            stuck.append(f"{top} (owned by {Path(top).owner()})")
            dirs.clear()  # its contents go with it, or not at all
    if not os.access(Path(checkout).parent, os.W_OK | os.X_OK):
        stuck.append(f"{Path(checkout).parent}, which holds the checkout")
    return stuck


def check(checkout: str, main: str, head: str, accepted: set[str]) -> Check:
    """What `checkout` holds that removing it would lose, or that would stop the removal halfway."""
    found = Check()
    found.changed = [path for _, path in _status(checkout, "--untracked-files=all")]
    local = run(["git", "-C", checkout, "rev-parse", "HEAD"])
    if local != head:
        found.unpushed = f"HEAD is {local[:9]}, not the PR's head {head[:9]}: commits the PR doesn't have would be lost"
    found.hidden = [
        line[2:]
        for line in run(["git", "-C", checkout, "ls-files", "-v"]).splitlines()
        if line[:1].islower() or line[:1] == "S"
    ]
    found.submodules = [
        line.split()[1]
        for line in run(["git", "-C", checkout, "submodule", "status"]).splitlines()
        if not line.startswith("-")
    ]
    found.unexpected = unexpected_ignored(checkout, main, accepted)
    found.undeletable = undeletable(checkout)
    return found


def stacked_on(branch: str, cwd: str) -> list[str]:
    """Branches git-town has stacked on `branch`."""
    out = try_run(["git", "config", "--get-regexp", r"^git-town-branch\..*\.parent$"], cwd=cwd) or ""
    return [
        key.removeprefix("git-town-branch.").removesuffix(".parent")
        for key, _, parent in (line.partition(" ") for line in out.splitlines())
        if parent == branch
    ]


def delete_branch(branch: str, cwd: str) -> None:
    """Delete the local branch and the git-town parent pointer `git branch -D` leaves behind."""
    run(["git", "branch", "-D", branch], cwd=cwd)
    if try_run(["git", "config", "--get", f"git-town-branch.{branch}.parent"], cwd=cwd) is not None:
        run(["git", "config", "--unset", f"git-town-branch.{branch}.parent"], cwd=cwd)
