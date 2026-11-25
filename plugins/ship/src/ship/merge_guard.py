"""PreToolUse hook: refuse a merge of a ship-tracked PR while its gate is shut.

On navari PR #5355 a review filed a med problem, the fix was left to the calling session, and that
session admin-merged the PR at the user's next message with the problem still open. Nothing between
the gate and the merge looked at the gate. Now this hook sees both ways an agent merges: `gh pr
merge` in any Bash command, and T3 Code's `pull_request_merge` tool, which T3 tells agents to use
instead of `gh` (#179). For a PR whose ship block names a tracking issue, it judges the gate at the
PR's head (what the merge would land) and denies the merge unless the gate is OPEN, naming what's
open. T3 refuses a merge unless the head is the `expectedHeadSha` it was given, so the PR's head is
that commit whenever the merge can happen. A T3 `stack` merge is refused: T3 reads the lower layers'
heads after this hook has run, so no check here can judge what it lands. An auto-merge lands whichever head passes CI, even one pushed after
the gate was judged, so it is refused on a tracked PR.

A user who has been told and still wants the merge says so, and the agent merges with
`SHIP_PAST_GATE="<the user's reason>"` in front of `gh pr merge` (a tool call has no such prefix, so
the refusal of a T3 merge points there too). The hook then lets it through and posts the reason and
the gate on the tracking issue first, so merging past the gate is a decision on the record, never a
silent skip. A merge by hand in GitHub's UI is the user's own and isn't seen here.

Fails closed: once a command is a merge of some PR, any error checking it denies the command with
that error, because letting it through would be the silent skip this exists to stop.
"""

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from . import feature, github, prbody, problems, shellscan
from .gitwiring import fetch_pr_head, has_object
from .state import parse_block

OVERRIDE = "SHIP_PAST_GATE"
# The name Claude Code gives T3 Code's merge tool; `hooks/hooks.json` matches the same name.
T3_MERGE = "mcp__t3-code__pull_request_merge"
# `gh pr merge` flags that take a value, so the value isn't read as the PR to merge.
_VALUE_FLAGS = {"-A", "--author-email", "-b", "--body", "-F", "--body-file", "-t", "--subject", "--match-head-commit"}
_REPO_FLAGS = {"-R", "--repo"}
# Words in front of a command that leave it the same command, in the same scope.
_RESERVED = {"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "{", "}", "!", "time"}
_SHELLS = {"sh", "bash", "zsh", "dash", "ksh"}
# A command that mentions a merge this loosely and can't be read is refused rather than guessed at.
_MENTIONS_MERGE = re.compile(r"\bgh\b.*\bpr\b\s+merge\b", re.DOTALL)


@dataclass(frozen=True)
class MergeCall:
    """One merge: the PR it names (None: the current branch's), its repo, the directory it runs in
    (after a command's own `cd`s), the `SHIP_PAST_GATE` reason if given, and whether it auto-merges."""

    target: str | None
    repo: str | None
    cwd: str
    override: str | None
    auto: bool = False


def _merge_args(args: list[str]) -> tuple[str | None, str | None]:
    """`(target, repo)` from the words after `gh pr merge`."""
    target = repo = None
    words = iter(args)
    for word in words:
        flag, eq, value = word.partition("=")
        if flag in _REPO_FLAGS:
            repo = value if eq else next(words, None)
        elif flag in _VALUE_FLAGS:
            if not eq:
                next(words, None)
        elif not word.startswith("-") and target is None:
            target = word
    return target, repo


def _gh_merge(words: list[str]) -> tuple[int, list[str], str | None] | None:
    """Where `words` run `gh pr merge`, behind whatever wrapper (`command`, `env`, `sudo`, `timeout
    10`, `xargs`, …): the index of `gh`, the words after `merge`, and a `--repo` given before it
    (`gh pr --repo X merge`). None if they don't."""
    for k, word in enumerate(words):
        if Path(word).name != "gh":
            continue
        repo, path, rest = None, [], iter(enumerate(words[k + 1 :], start=k + 1))
        for j, arg in rest:
            flag, eq, value = arg.partition("=")
            if flag in _REPO_FLAGS:
                repo = value if eq else next(rest, (None, None))[1]
            elif not arg.startswith("-"):
                path.append(arg)
                if path == ["pr", "merge"]:
                    return k, words[j + 1 :], repo
                if path != ["pr"]:
                    break
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
        elif (merge := _gh_merge(words)) is not None and "--disable-auto" not in merge[1]:
            at, args, global_repo = merge
            env |= dict(w.partition("=")[::2] for w in words[:at] if _is_assignment(w))
            target, repo = _merge_args(args)
            calls.append(
                MergeCall(
                    target=target,
                    repo=repo or global_repo,
                    cwd=cwd,
                    override=env.get(OVERRIDE) or None,
                    auto="--auto" in args,
                )
            )
    return calls, cwd


def _is_assignment(word: str) -> bool:
    name, eq, _ = word.partition("=")
    return bool(eq) and name.isidentifier()


def merge_calls(command: str, cwd: str) -> list[MergeCall]:
    """Every `gh pr merge` in `command`, run from `cwd`. Raises ValueError for a command it can't read."""
    return _walk(shellscan.parse(command), cwd)[0]


def t3_merge_call(tool_input: dict, cwd: str) -> MergeCall:
    """The merge a `pull_request_merge` call makes. Its PR defaults to the thread's branch, the
    current branch here; in another linked environment that branch is out of sight, so the PR has to
    be named. Raises ValueError when it isn't."""
    number, repo = tool_input.get("number"), tool_input.get("repository")
    if tool_input.get("environmentId") and (number is None or repo is None):
        raise ValueError("this merge runs in another linked environment, so name the PR with `number` and `repository`")
    if tool_input.get("stack") is True:
        raise ValueError(
            "a stack merge lands layers whose heads T3 reads after this check, so merge the PRs one at a time, "
            "bottom up, each with its own `expectedHeadSha`"
        )
    return MergeCall(
        target=None if number is None else str(number),
        repo=repo,
        cwd=cwd,
        override=None,
        auto=tool_input.get("auto") is True,
    )


def _past_gate(pr: dict) -> str:
    return (
        f'Only if the user, told this, still wants it merged now: `{OVERRIDE}="<their reason>" gh pr merge '
        f"{pr['number']} --repo {pr['repo']}`; the reason goes on the tracking issue."
    )


def _refusal(pr: dict, issue: int, decision, blocking: list[dict]) -> str:
    listed = "\n".join(f"  #{s['number']} [{problems.severity(s)}] {s['title']}" for s in blocking) or "  none"
    return (
        f"ship refuses to merge PR #{pr['number']}: its gate (feature #{issue}) is {decision}.\n"
        f"Open problems, every one blocking:\n{listed}\n"
        f"Nothing ships as debt. Close them with `Skill(ship:ship)`, which fixes each one and reviews again "
        f"until the gate is OPEN. Tell the user what is open. {_past_gate(pr)}"
    )


def _auto_refusal(pr: dict, issue: int) -> str:
    return (
        f"ship refuses to auto-merge PR #{pr['number']} (feature #{issue}): auto-merge lands whichever head "
        f"passes CI, even one pushed after the gate was judged. Wait for the checks, then merge without auto. "
        f"{_past_gate(pr)}"
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
    head = pr["headRefOid"]
    if not has_object(head):
        fetch_pr_head(pr["number"])
    state = parse_block(github.get_issue_body(issue))
    subs = github.sub_issues(issue)
    decision = feature.decide(pr["headRefName"], state, subs, False, head=head)
    if decision.settled and not call.auto:
        return None
    blocking = problems.blocking(subs)
    if call.override is None:
        return _auto_refusal(pr, issue) if decision.settled else _refusal(pr, issue, decision, blocking)
    how = " with auto-merge" if call.auto else ""
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
