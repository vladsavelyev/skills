"""A tracked feature as GitHub and git hold it: its state, its problems, and its merge gate.

The query every caller of the gate shares: the CLI's commands, the Stop hook and the merge guard.
It raises `Refusal` for an expected condition it can't go past (a branch with no feature issue, a
base origin lacks) rather than ending the process, so each caller says it its own way: the CLI
prints it and exits, the merge guard denies the merge, the Stop hook reports it.
"""

from dataclasses import dataclass

from . import budget, gate, github, problems
from .errors import Refusal
from .gitwiring import (
    auto_merge_tree,
    branch_head_sha,
    changed_paths,
    commits_since,
    feature_issue,
    fetch_pr_head,
    has_object,
    is_ancestor,
    remote_ref,
    sensitive_patterns,
)
from .state import budget_run, fixes, parse_block, redesigns


def resolve_issue(branch: str) -> int:
    issue = feature_issue(branch)
    if issue is None:
        raise Refusal(
            f"No feature issue wired for branch '{branch}'. "
            f"Run `feature adopt` first, or set branch.{branch}.feature-issue."
        )
    return issue


def load_state(branch: str) -> tuple[int, dict, str]:
    """Return (issue_number, state_dict, raw_body) for a branch's feature issue."""
    issue = resolve_issue(branch)
    body = github.get_issue_body(issue)
    return issue, parse_block(body), body


@dataclass(frozen=True)
class Snapshot:
    """A feature as one command reads it: its tracking issue, the state in its body, and its problems."""

    issue: int
    state: dict
    body: str
    subs: list[dict]

    def problem(self, number: int) -> dict:
        """`number` as one of the feature's problems. Refuses any other number: resolving and
        disposing edit exactly the state the gate reads, and pointed at the wrong number (a typo, a
        PR number, the feature issue itself) they would quietly change what blocks a merge or close
        something unrelated."""
        sub = next((s for s in self.subs if s["number"] == number), None)
        if sub is None:
            raise Refusal(
                f"#{number} is not a problem sub-issue of feature #{self.issue} — refusing to touch it. "
                f"Run `feature problem list` to see this feature's problems."
            )
        return sub


def snapshot(branch: str) -> Snapshot:
    issue, state, body = load_state(branch)
    return Snapshot(issue, state, body, github.sub_issues(issue))


def resolve_budget(state: dict) -> tuple[int, bool, list[str]]:
    """The feature's review-round budget: an explicit override, else auto-sized from the PR diff.

    Auto-sizing happens at query time rather than being frozen into the state block, so a branch
    that grows from 20 to 900 lines earns its extra round without anyone re-running a command.
    """
    explicit = state["review_budget"]
    if explicit is not None:
        return explicit, True, [f"explicit override: {explicit} round(s)"]
    if state["pr"] is None:
        return budget.BASE_ROUNDS, False, ["no PR linked yet, so no diff to size — base budget for now"]
    files, lines = github.pr_diff_stats(state["pr"])
    hits = budget.sensitive_matches(github.pr_changed_paths(state["pr"], expected=files), sensitive_patterns())
    rounds, why = budget.auto_rounds(changed_files=files, changed_lines=lines, sensitive_hits=hits)
    return rounds, False, why


def pr_merged(state: dict) -> bool:
    """Whether the feature's linked PR has merged — the one fact that ends the review loop."""
    return state["pr"] is not None and github.pr_state(state["pr"]) == "MERGED"


def review_head(branch: str, state: dict, merged: bool) -> str:
    """The commit the last review has to cover. Once the PR has merged that is the PR's head — the code
    that went in; the local branch may lag behind it (pushed from another checkout) or be gone."""
    return github.pr_head_sha(state["pr"]) if merged else branch_head_sha(branch)


def _base_heads(state: dict) -> list[str]:
    """The commits whose history counts as the PR's base: its base branch on origin and, once a
    parent it was stacked on has merged, that parent's PR head (a squash merge leaves the parent's
    commits out of the base branch, but its PR reviewed them)."""
    base = github.pr_base_branch(state["pr"])
    base_ref = remote_ref(base)
    if base_ref is None:
        raise Refusal(
            f"PR #{state['pr']} targets '{base}', which origin doesn't have: can't tell its merges of the base apart."
        )
    heads = [base_ref]
    if state["base"] != base and (parent := github.merged_pr_for_branch(state["base"])) is not None:
        fetch_pr_head(parent)
        heads.append(github.pr_head_sha(parent))
    return heads


def _base_merge_auto_trees(commits: list[tuple[str, list[str]]], state: dict) -> dict[str, str]:
    merges = [(sha, parents) for sha, parents in commits if len(parents) == 2]
    heads = _base_heads(state) if merges else []
    return {
        sha: auto_merge_tree(*parents)
        for sha, parents in merges
        if any(is_ancestor(parents[1], head) for head in heads)
    }


def own_changes(commits: list[tuple[str, list[str]]], state: dict) -> list[tuple[str, list[str]]]:
    """Each of `commits` with the paths of what it adds to the PR's code: its diff from its first
    parent or, for a merge of the base, from the merge git makes by itself (so only what was resolved
    by hand)."""
    auto = _base_merge_auto_trees(commits, state)
    return [(sha, changed_paths(auto.get(sha, parents[0]), sha)) for sha, parents in commits]


def base_merges_needing_review(commits: list[tuple[str, list[str]]], state: dict) -> list[str]:
    changes = [(sha, changed_paths(tree, sha)) for sha, tree in _base_merge_auto_trees(commits, state).items()]
    return gate.needs_review(changes, low_fixes=set(), sensitive=sensitive_patterns())


def unreviewed(state: dict, head: str, subs: list[dict]) -> list[str]:
    """The commits since the last review that a review still has to see (`gate.needs_review`)."""
    last = state["last_review"]
    if last is None or gate.covers_head(last, head):
        return []
    if not has_object(head):
        # A merged PR's head, read from GitHub, need not be in this clone at all.
        fetch_pr_head(state["pr"])
    changes = own_changes(commits_since(last["sha"], head), state)
    low_fixes = problems.low_fix_commits(fixes(state), subs)
    return gate.needs_review(changes, low_fixes=low_fixes, sensitive=sensitive_patterns())


def dismissed(subs: list[dict]) -> frozenset[int]:
    """The problems closed as not relevant (`wontfix`): no find among them recurs (`gate.recurrences`)."""
    return frozenset(s["number"] for s in subs if s["state"] == "CLOSED" and problems.is_wontfix(s))


def decide(branch: str, state: dict, subs: list[dict], merged: bool, *, head: str | None = None) -> gate.GateDecision:
    """The merge gate for `branch`, from its state and its problems (`github.sub_issues`), judged at
    `head` (default `review_head`)."""
    rounds, pinned, _ = resolve_budget(state)
    return gate.evaluate(
        blocking_open=len(problems.blocking(subs)),
        last_review=state["last_review"],
        history=state["review_history"],
        budget=rounds,
        unreviewed=unreviewed(state, head or review_head(branch, state, merged), subs),
        budget_answers=budget_run(state) if pinned else None,
        redesigns=redesigns(state),
        dismissed=dismissed(subs),
        pr_merged=merged,
    )


def gate_for(branch: str) -> tuple[int, dict, list[dict], gate.GateDecision]:
    issue, state, _ = load_state(branch)
    subs = github.sub_issues(issue)
    return issue, state, problems.blocking(subs), decide(branch, state, subs, pr_merged(state))


def blocking_refs(blocking: list[dict]) -> str:
    return ", ".join(f"#{s['number']} [{problems.severity(s)}]" for s in blocking) or "none"
