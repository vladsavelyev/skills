"""PreToolUse hook: refuse `gh pr merge` of a ship-tracked PR while its gate is shut.

On navari PR #5355 a review filed a med problem, the fix was left to the calling session, and that
session admin-merged the PR at the user's next message with the problem still open. Nothing between
the gate and the merge looked at the gate. Now this hook reads every Bash command an agent runs; on
`gh pr merge` of a PR whose ship block names a tracking issue, it judges the gate at the PR's head
(what the merge would land) and denies the command unless the gate is OPEN, naming what's open.

A user who has been told and still wants the merge says so, and the agent re-runs the command with
`SHIP_PAST_GATE="<the user's reason>"` in front of `gh`. The hook then lets it through and posts the
reason and the gate on the tracking issue first, so merging past the gate is a decision on the
record, never a silent skip. A merge by hand in GitHub's UI is the user's own and isn't seen here.

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
    """One `gh pr merge` in a command: the PR it names (None: the current branch's), the `--repo`, the
    directory it runs in after the command's own `cd`s, and the `SHIP_PAST_GATE` reason if given."""

    target: str | None
    repo: str | None
    cwd: str
    override: str | None


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
        elif (merge := _gh_merge(words)) is not None:
            at, args, global_repo = merge
            env |= dict(w.partition("=")[::2] for w in words[:at] if _is_assignment(w))
            target, repo = _merge_args(args)
            calls.append(
                MergeCall(target=target, repo=repo or global_repo, cwd=cwd, override=env.get(OVERRIDE) or None)
            )
    return calls, cwd


def _is_assignment(word: str) -> bool:
    name, eq, _ = word.partition("=")
    return bool(eq) and name.isidentifier()


def merge_calls(command: str, cwd: str) -> list[MergeCall]:
    """Every `gh pr merge` in `command`, run from `cwd`. Raises ValueError for a command it can't read."""
    return _walk(shellscan.parse(command), cwd)[0]


def _refusal(pr: dict, issue: int, decision, blocking: list[dict]) -> str:
    listed = "\n".join(f"  #{s['number']} [{problems.severity(s)}] {s['title']}" for s in blocking) or "  none"
    return (
        f"ship refuses to merge PR #{pr['number']}: its gate (feature #{issue}) is {decision}.\n"
        f"Open problems, every one blocking:\n{listed}\n"
        f"Nothing ships as debt. Close them with `Skill(ship:ship)`, which fixes each one and reviews again "
        f"until the gate is OPEN. Tell the user what is open. Only if the user, told this, still wants it "
        f'merged now, re-run the command with {OVERRIDE}="<their reason>" in front of `gh`; the reason '
        f"goes on the tracking issue."
    )


def check(call: MergeCall) -> str | None:
    """Why `call` must not run, or None if it may: the PR isn't ship's, its gate is OPEN, or the
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
    if decision.settled:
        return None
    blocking = problems.blocking(subs)
    if call.override is None:
        return _refusal(pr, issue, decision, blocking)
    github.comment(
        issue,
        f"⚠️ **Merging PR #{pr['number']} past the gate**, at the user's request: {call.override}\n\n"
        f"Gate: {decision}\n\n"
        f"Open problems, every one blocking: {len(blocking)} — {feature.blocking_refs(blocking)}",
    )
    return None


def main() -> None:
    payload = json.load(sys.stdin)
    if payload.get("tool_name") != "Bash":
        return
    command = payload["tool_input"]["command"]
    try:
        calls = merge_calls(command, payload["cwd"])
    except ValueError as exc:
        if _MENTIONS_MERGE.search(command):
            _deny(
                [
                    f"ship can't read this command ({exc}), and it may run `gh pr merge`: run the merge as a plain command."
                ]
            )
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
