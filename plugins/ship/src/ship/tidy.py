"""Removing a closed-out feature's checkout and branch, once they hold nothing the PR doesn't.

What a checkout holds beyond its commits is either work, which removing it would delete without a word, or what
running the project made there. Work blocks the removal: a modified or untracked file, a commit the PR doesn't have,
a file hidden from `status` by assume-unchanged or skip-worktree, a repository inside it. An ignored path is what the project made when the main checkout has it too (`.env`, a local config, logs: every
checkout that runs the project carries them), or when it is a cache any checkout rebuilds (`.venv`, `node_modules`,
`__pycache__`). Any other ignored path, eval results say, may be the user's, so it blocks until they accept it.

A repository inside the checkout (a clone, a worktree, a submodule, a bare repo) blocks wherever it sits, whatever
state it is in, until the user accepts it: its work is its own (an uncommitted edit, a stash, a commit a reflog
alone holds, a `.env`), and no state git reports proves it holds none. Where it sits proves nothing either: `pip
install -e git+…` clones into `.venv/src` to be edited. The one `.git` that goes with its folder is one git can't
open sitting in a cache (pygaljs ships a submodule's pointer without its target): there is no repository there, and
the cache is rebuilt. Outside a cache, the same `.git` may be all that's left of a worktree moved by hand, so it
blocks.

Every folder must also be one we can empty: a container that ran as root leaves root-owned files on a Linux host,
and `git worktree remove` unregisters the checkout before it fails on them, leaving the rest on disk. Those are
found first, and nothing is removed while any remain.
"""

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .shell import CommandFailed, run, try_run

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
    head_mismatch: str | None = None
    hidden: list[str] = field(default_factory=list)
    nested: list[str] = field(default_factory=list)
    worktrees: list[str] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    undeletable: list[str] = field(default_factory=list)

    @property
    def clear(self) -> bool:
        return not (
            self.changed
            or self.head_mismatch
            or self.hidden
            or self.nested
            or self.worktrees
            or self.unexpected
            or self.undeletable
        )

    def __str__(self) -> str:
        lines = []
        if self.head_mismatch:
            lines.append(self.head_mismatch)
        for title, paths in (
            ("modified or untracked (commit and push them, or delete them)", self.changed),
            ("hidden from `git status` by assume-unchanged or skip-worktree", self.hidden),
            ("worktrees registered inside it: `git worktree remove` it first, once it holds nothing", self.worktrees),
            (
                "repositories inside it, or what's left of one, whose own work would go with it: pass --accept <path> for each the user OKs deleting",
                self.nested,
            ),
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
    # Git prints an ignored folder as `results/`; take it with or without the slash, but only that path, not what's under.
    exact = {path.rstrip("/") for path in accepted}
    return [
        path
        for path in ignored
        if not rebuildable(path) and not (Path(main) / path).exists() and path.rstrip("/") not in exact
    ]


def _looks_like_git_dir(dirs: list[str], files: list[str]) -> bool:
    """Git's own test of a folder that is a repository's git data: a bare repo, a mirror, a `.git` folder."""
    return "HEAD" in files and {"objects", "refs"} <= set(dirs)


def _opens(git_dir: str) -> bool:
    """Whether git opens `git_dir` (a folder, or a `.git` file pointing at one) as a repository.

    `--git-dir` keeps git from looking further up for the checkout around it, and `safe.directory` from refusing a
    repository someone else owns as if it weren't one. Only git's "there's no repository here" answers are a no.
    """
    cmd = ["git", "-c", "safe.directory=*", f"--git-dir={git_dir}", "rev-parse", "--git-dir"]
    result = subprocess.run(cmd, capture_output=True, text=True, env=os.environ | {"LC_ALL": "C"})
    if result.returncode == 0:
        return True
    if result.returncode == 128 and ("not a git repository" in result.stderr or "invalid gitfile" in result.stderr):
        return False
    raise CommandFailed(result.returncode, cmd, result.stdout, result.stderr)


def scan(checkout: str, accepted: set[str]) -> tuple[list[str], list[str]]:
    """Walk `checkout` once for (folders we can't empty, repositories inside it the user hasn't accepted).

    Emptying a folder takes listing it and writing to it. A folder we can't even list is one `os.walk` would silently
    skip, so its error is collected too: skipped, it would pass the check and `git worktree remove` would fail on it
    halfway. The walk goes on into repositories and their git data, since all of it goes with the checkout: a
    root-owned object, or a repository inside a repository, counts there too. Only a git dir's own folders
    (`modules/`, `worktrees/`, `refs/`) aren't searched for repositories, since they'd look like some.
    """
    stuck, nested, git_dirs = [], [], []

    def unscannable(error: OSError) -> None:
        stuck.append(f"{error.filename} (owned by {Path(error.filename).owner()}): {error.strerror}")

    for top, dirs, files in os.walk(checkout, onerror=unscannable):
        here = Path(top)
        if top == checkout:
            if ".git" in dirs:  # the main checkout's own git dir; a linked worktree's `.git` is a file
                dirs.remove(".git")
        elif not any(here.is_relative_to(git_dir) for git_dir in git_dirs):
            rel = os.path.relpath(top, checkout)
            if _looks_like_git_dir(dirs, files):
                git_dirs.append(here)
                nested.append(rel)
            elif ".git" in dirs or ".git" in files:
                git_dirs.append(here / ".git")
                if not rebuildable(rel) or _opens(str(here / ".git")):
                    nested.append(rel)
        if (dirs or files) and not os.access(top, os.R_OK | os.W_OK | os.X_OK):
            stuck.append(f"{top} (owned by {here.owner()})")
            dirs.clear()  # its contents go with it, or not at all
    if not os.access(Path(checkout).parent, os.W_OK | os.X_OK):
        stuck.append(f"{Path(checkout).parent}, which holds the checkout")
    exact = {path.rstrip("/") for path in accepted}
    return stuck, [rel for rel in nested if rel not in exact]


def check(checkout: str, main: str, head: str, accepted: set[str]) -> Check:
    """What `checkout` holds that removing it would lose, or that would stop the removal halfway."""
    found = Check()
    found.changed = [path for _, path in _status(checkout, "--untracked-files=all")]
    local = run(["git", "-C", checkout, "rev-parse", "HEAD"])
    if local != head:
        found.head_mismatch = (
            f"HEAD is {local[:9]}, not the PR's head {head[:9]}: commits the PR doesn't have would be lost"
        )
    found.hidden = [
        line[2:]
        for line in run(["git", "-C", checkout, "ls-files", "-v"]).splitlines()
        if line[:1].islower() or line[:1] == "S"
    ]
    found.unexpected = unexpected_ignored(checkout, main, accepted)
    found.undeletable, found.nested = scan(checkout, accepted)
    # A worktree git has registered under this one, even one we can't read. Deleting it would leave git a registration
    # to prune and a branch marked checked out, so git, not --accept, removes it.
    registered = [
        os.path.relpath(line.removeprefix("worktree "), checkout)
        for line in run(["git", "-C", main, "worktree", "list", "--porcelain"]).splitlines()
        if line.startswith("worktree ") and Path(checkout) in Path(line.removeprefix("worktree ")).parents
    ]
    found.nested = [path for path in found.nested if path not in registered]
    found.worktrees = registered
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
