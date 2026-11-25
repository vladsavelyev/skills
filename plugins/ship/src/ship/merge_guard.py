"""PreToolUse hook: refuse a merge of a ship-tracked PR while its gate is shut.

On navari PR #5355 a review filed a med problem, the fix was left to the calling session, and that
session admin-merged the PR at the user's next message with the problem still open. Nothing between
the gate and the merge looked at the gate. Now this hook sees both ways an agent merges: `gh pr
merge` in any Bash command, and T3 Code's `pull_request_merge` tool, which T3 tells agents to use
instead of `gh` (#179). For a PR whose ship block names a tracking issue, it judges the gate at the
commit the merge would land (the head it pins, else the PR's) and denies the merge unless the gate
is OPEN, naming what's open. A merge GitHub defers until CI passes (auto-merge, a merge queue) lands
whichever head passes, even one pushed after the gate was judged, so it is refused on a tracked PR.
A T3 `stack` merge is refused on any PR: T3 reads the lower layers' heads after this hook has run.

A user who has been told and still wants the merge says so, and the agent re-runs its `gh pr
merge` with `SHIP_PAST_GATE="<the user's reason>"` in front of `gh`. A tool call can't carry that
prefix, so the refusal of a T3 merge prints the `gh pr merge` that makes the same merge. The hook
then lets it through and posts the reason and the gate on the tracking issue first, so merging past
the gate is a decision on the record, never a silent skip. A merge by hand in GitHub's UI is the
user's own and isn't seen here.

Fails closed: once a command is a merge of some PR, any error checking it denies the command with
that error, because letting it through would be the silent skip this exists to stop.
"""

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import feature, github, prbody, problems, shellscan
from .gitwiring import fetch_pr_head, has_object
from .state import parse_block

OVERRIDE = "SHIP_PAST_GATE"
T3_MERGE = "mcp__t3-code__pull_request_merge"
# The flags `gh pr merge` takes (with `gh pr`'s `--repo`) by long name: those that take a value, and
# each short name. gh's flag parser reads a boolean's `=value` the way Go's `strconv.ParseBool` does.
_VALUE_FLAGS = {"author-email", "body", "body-file", "match-head-commit", "repo", "subject"}
_SHORT_FLAGS = {"A": "author-email", "b": "body", "F": "body-file", "d": "delete-branch", "h": "help", "m": "merge"}
_SHORT_FLAGS |= {"r": "rebase", "R": "repo", "s": "squash", "t": "subject"}
_TRUE = {"1", "t", "T", "true", "TRUE", "True"}
_FALSE = {"0", "f", "F", "false", "FALSE", "False"}
# The `gh pr merge` flag for each merge method of T3's `pull_request_merge`. GitHub has no fast-forward.
_GH_METHODS = {"merge": "--merge", "squash": "--squash", "rebase": "--rebase"}
# Words in front of a command that leave it the same command, in the same scope.
_RESERVED = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "{", "}", "!", "time"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
# A command that mentions a merge this loosely and can't be read is refused rather than guessed at.
_MENTIONS_MERGE = re.compile(r"\bgh\b.*\bpr\b\s+merge\b", re.DOTALL)


@dataclass(frozen=True)
class MergeCall:
    """One merge: the PR it names (None: the current branch's), its repo, the directory it runs in
    (after a command's own `cd`s), the `SHIP_PAST_GATE` reason if given, whether it auto-merges,
    whether it skips a merge queue (`gh pr merge --admin`), the head it pins (None: whatever the
    PR's head is), its merge method (None: none named), and whether it is a `gh pr merge` in Bash or
    a call of T3's `pull_request_merge`."""

    target: str | None
    repo: str | None
    cwd: str
    override: str | None
    auto: bool = False
    admin: bool = False
    head: str | None = None
    method: str | None = None
    origin: Literal["gh", "t3"] = "gh"


def _gh_args(words: list[str]) -> tuple[list[str], dict[str, str | bool], list[str]]:
    """The positional words of a gh command line, its flags by long name, and the flag words gh
    would refuse, read the way gh reads them for `gh pr merge`: a value flag takes `=value`, the rest
    of a short cluster or the next word, and a boolean takes an optional `=true` or `=false`."""
    positional: list[str] = []
    flags: dict[str, str | bool] = {}
    refused: list[str] = []
    rest = iter(words)
    for word in rest:
        if word == "--":
            positional += rest
            continue
        if not word.startswith("-") or word == "-":
            positional.append(word)
            continue
        for name, attached in _flag_words(word):
            if name in _VALUE_FLAGS:
                value = next(rest, None) if attached is None else attached
            elif attached is None:
                value = True
            elif attached in _TRUE or attached in _FALSE:
                value = attached in _TRUE
            else:
                value = None
            if value is None:
                refused.append(word)
            else:
                flags[name] = value
    return positional, flags, refused


def _flag_words(word: str) -> list[tuple[str, str | None]]:
    """The flags `word` sets by long name, each with the text attached to it (None: none). In a short
    cluster, a value flag or a `=` takes the rest of the word."""
    if word.startswith("--"):
        name, eq, attached = word[2:].partition("=")
        return [(name, attached if eq else None)]
    found: list[tuple[str, str | None]] = []
    for k, letter in enumerate(word[1:], start=2):
        name, tail = _SHORT_FLAGS.get(letter, letter), word[k:]
        if name in _VALUE_FLAGS or tail.startswith("="):
            return [*found, (name, tail.removeprefix("=") if tail else None)]
        found.append((name, None))
    return found


def _gh_merge(words: list[str], cwd: str, env: dict[str, str]) -> MergeCall | None:
    """The merge `words` run with `gh pr merge`, behind whatever wrapper (`command`, `env`, `sudo`,
    `timeout 10`, `xargs`, …), or None if they run none: `--help` and `--disable-auto` merge nothing.
    Raises ValueError for a merge with a flag gh would refuse, rather than read it as another."""
    for k, word in enumerate(words):
        if Path(word).name != "gh":
            continue
        positional, flags, refused = _gh_args(words[k + 1 :])
        if positional[:2] != ["pr", "merge"]:
            continue
        if refused:
            raise ValueError(f"gh would refuse `{refused[0]}`")
        if flags.get("help") is True or flags.get("disable-auto") is True:
            return None
        env = env | dict(w.partition("=")[::2] for w in words[:k] if _is_assignment(w))
        return MergeCall(
            target=next(iter(positional[2:]), None),
            repo=flags.get("repo") or None,
            cwd=cwd,
            override=env.get(OVERRIDE) or None,
            auto=flags.get("auto") is True,
            admin=flags.get("admin") is True,
            head=flags.get("match-head-commit") or None,
            method=next((m for m in _GH_METHODS if flags.get(m) is True), None),
        )
    return None


def _shell_script(words: list[str]) -> str | None:
    """The script a `sh -c`, `bash -lc` (and so on) in `words` runs, or None."""
    for k, word in enumerate(words):
        if Path(word).name in _SHELLS:
            for j, arg in enumerate(words[k + 1 :], start=k + 1):
                if arg.startswith("-") and not arg.startswith("--") and "c" in arg and j + 1 < len(words):
                    return words[j + 1]
    return None


def _walk(script: shellscan.Script, cwd: str) -> tuple[list[MergeCall], str]:
    """The merges `script` runs from `cwd`, and the directory it ends in. A subshell, a substitution
    and a `sh -c` get their own scope, so their `cd`s stay in them; `eval` runs in this one."""
    calls: list[MergeCall] = []
    for item in script.items:
        if isinstance(item, shellscan.Script):
            calls += _walk(item, cwd)[0]
            continue
        for substitution in item.substitutions:
            calls += _walk(substitution, cwd)[0]
        words = list(item.words)
        while words and words[0] in _RESERVED:
            words.pop(0)
        env = {}
        while words and _is_assignment(words[0]):
            name, _, value = words.pop(0).partition("=")
            env[name] = value
        if not words:
            continue
        if words[0] == "cd" and len(words) == 2:
            cwd = os.path.normpath(os.path.join(cwd, os.path.expanduser(words[1])))
        elif words[0] == "eval":
            found, cwd = _walk(shellscan.parse(" ".join(words[1:])), cwd)
            calls += found
        elif (inner := _shell_script(words)) is not None:
            calls += _walk(shellscan.parse(inner), cwd)[0]
        elif (call := _gh_merge(words, cwd, env)) is not None:
            calls.append(call)
    return calls, cwd


def _is_assignment(word: str) -> bool:
    name, eq, _ = word.partition("=")
    return bool(eq) and name.isidentifier()


def merge_calls(command: str, cwd: str) -> list[MergeCall]:
    """Every `gh pr merge` in `command`, run from `cwd`. Raises ValueError for a command it can't read."""
    return _walk(shellscan.parse(command), cwd)[0]


def t3_merge_call(tool_input: dict, cwd: str) -> MergeCall:
    """The merge a `pull_request_merge` call makes. T3's default PR is its thread's branch, which
    this hook can't see, so the PR has to be named. Raises ValueError when it isn't, or for a stack."""
    number, repo = tool_input.get("number"), tool_input.get("repository")
    if number is None or repo is None:
        raise ValueError("name the PR with `number` and `repository`")
    if tool_input.get("stack") is True:
        raise ValueError(
            "a stack merge lands layers whose heads T3 reads after this check, so merge the PRs one at a time, "
            "bottom up, each with its own `expectedHeadSha`"
        )
    return MergeCall(
        target=str(number),
        repo=repo,
        cwd=cwd,
        override=None,
        auto=tool_input.get("auto") is True,
        head=tool_input.get("expectedHeadSha"),
        method=tool_input.get("method") or "merge",
        origin="t3",
    )


def _past_gate(call: MergeCall, pr: dict, head: str) -> str:
    """How the user's merge runs past the gate: its `gh pr merge` with the reason in front, or for a
    T3 call, the `gh pr merge` that makes it, pinned to the head the gate was judged at."""
    if call.origin == "gh":
        how = f're-run the command with `{OVERRIDE}="<their reason>"` in front of `gh`'
    else:
        flag = _GH_METHODS.get(call.method, "--merge|--squash|--rebase")
        auto = " --auto" if call.auto else ""
        how = (
            f'run `{OVERRIDE}="<their reason>" gh pr merge {pr["number"]} --repo {pr["repo"]} {flag}{auto} '
            f"--match-head-commit {head}`"
        )
        if call.method not in _GH_METHODS:
            how += f" with the method the user picks: GitHub has no {call.method} merge"
    return f"Only if the user, told this, still wants it merged now, {how}; the reason goes on the tracking issue."


def _refusal(pr: dict, issue: int, decision, blocking: list[dict], past_gate: str) -> str:
    listed = "\n".join(f"  #{s['number']} [{problems.severity(s)}] {s['title']}" for s in blocking) or "  none"
    return (
        f"ship refuses to merge PR #{pr['number']}: its gate (feature #{issue}) is {decision}.\n"
        f"Open problems, every one blocking:\n{listed}\n"
        f"Nothing ships as debt. Close them with `Skill(ship:ship)`, which fixes each one and reviews again "
        f"until the gate is OPEN. Tell the user what is open. {past_gate}"
    )


def _deferral(call: MergeCall, pr: dict) -> str | None:
    """How GitHub defers the merge to whenever CI passes, or None if it merges now. A base that
    requires a merge queue is joined by auto-merge (T3 always enables it there, `gh pr merge` when
    checks are pending), and a push needn't take a PR back out of either."""
    if call.auto:
        return "with auto-merge"
    if not call.admin and github.merge_queue_enabled(pr["number"], pr["repo"]):
        return "through its base's merge queue"
    return None


def _deferred_refusal(pr: dict, issue: int, deferral: str, call: MergeCall, past_gate: str) -> str:
    wait = "Wait for the checks, then merge without auto. " if call.auto else ""
    return (
        f"ship refuses to merge PR #{pr['number']} {deferral} (feature #{issue}): GitHub then lands whichever "
        f"head passes CI, even one pushed after the gate was judged. {wait}{past_gate}"
    )


def check(call: MergeCall) -> str | None:
    """Why `call` must not run, or None if it may: its PR isn't ship's, its gate is OPEN, or the
    user's reason for merging past it is now on the record."""
    os.chdir(call.cwd)
    github.use_owner_account()
    pr = github.pr_view(call.target, repo=call.repo)
    issue = prbody.tracking_issue(pr["body"])
    if issue is None:
        issue = next((n for n in prbody.part_of_references(pr["body"]) if "feature" in github.issue_labels(n)), None)
    if issue is None or pr["state"] != "OPEN":
        return None
    if (here := github.current_repo()) != pr["repo"]:
        return (
            f"ship can't check the gate of PR #{pr['number']} from {call.cwd}, a checkout of {here}: run the "
            f"merge from a checkout of {pr['repo']}."
        )
    head = call.head or pr["headRefOid"]
    if not has_object(head):
        fetch_pr_head(pr["number"])
    state = parse_block(github.get_issue_body(issue))
    subs = github.sub_issues(issue)
    decision = feature.decide(pr["headRefName"], state, subs, False, head=head)
    deferral = _deferral(call, pr)
    if decision.settled and deferral is None:
        return None
    blocking = problems.blocking(subs)
    if call.override is None:
        past_gate = _past_gate(call, pr, head)
        return (
            _deferred_refusal(pr, issue, deferral, call, past_gate)
            if decision.settled
            else _refusal(pr, issue, decision, blocking, past_gate)
        )
    how = f" {deferral}" if deferral else ""
    github.comment(
        issue,
        f"⚠️ **Merging PR #{pr['number']}{how} past the gate**, at the user's request: {call.override}\n\n"
        f"Gate: {decision}\n\n"
        f"Open problems, every one blocking: {len(blocking)} — {feature.blocking_refs(blocking)}",
    )
    return None


def main() -> None:
    payload = json.load(sys.stdin)
    tool, tool_input = payload.get("tool_name"), payload["tool_input"]
    if tool == T3_MERGE:
        try:
            calls = [t3_merge_call(tool_input, payload["cwd"])]
        except ValueError as exc:
            _deny([f"ship refuses this merge: {exc}."])
            return
    elif tool == "Bash":
        command = tool_input["command"]
        try:
            calls = merge_calls(command, payload["cwd"])
        except ValueError as exc:
            if _MENTIONS_MERGE.search(command):
                _deny(
                    [
                        f"ship can't read this command ({exc}), and it may run `gh pr merge`: run the merge as a "
                        f"plain command."
                    ]
                )
            return
    else:
        return
    refusals = []
    for call in calls:
        try:
            refusal = check(call)
        except Exception as exc:  # fail closed: an unchecked merge is the very failure this hook stops
            refusal = f"ship couldn't check the merge gate, so it refuses the merge: {exc}"
        if refusal:
            refusals.append(refusal)
    if refusals:
        _deny(refusals)


def _deny(refusals: list[str]) -> None:
    decision = {"hookEventName": "PreToolUse", "permissionDecision": "deny"}
    print(json.dumps({"hookSpecificOutput": decision | {"permissionDecisionReason": "\n\n".join(refusals)}}))


if __name__ == "__main__":
    main()
