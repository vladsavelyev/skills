"""GitHub layer: issues, sub-issues, labels, PRs — via `gh` and GraphQL.

Sub-issues have no clean `gh` CLI surface yet, so parent/child linkage goes through GraphQL.
The `addSubIssue` mutation is still preview-ish; its behavior is pinned here.
"""

import json
import os
import re

from . import prbody
from .shell import run

LABELS = [
    ("feature", "0e8a16", "Feature tracking issue"),
    ("problem", "d93f0b", "Review-found problem"),
    ("sev:high", "b60205", "High severity problem (blocks the merge gate)"),
    ("sev:med", "fbca04", "Medium severity problem (blocks the merge gate)"),
    ("sev:low", "0e8a16", "Low severity problem (tracked debt, does not block)"),
    ("deferred", "c5def5", "Real problem, deliberately not fixed in this PR (reason on the issue)"),
    ("rejected", "cfd3d7", "Not a real problem — false positive or by design (reason on the issue)"),
    ("follow-up", "1d76db", "Shipped unfixed, kept open as a backlog issue (reason on the issue)"),
    ("wontfix", "ffffff", "Real problem, closed as not planned (reason on the issue)"),
]


def init_labels() -> None:
    """Create the workflow labels. --force makes it idempotent (updates if present)."""
    for name, color, desc in LABELS:
        run(
            [
                "gh",
                "label",
                "create",
                name,
                "--color",
                color,
                "--description",
                desc,
                "--force",
            ]
        )


def _node_id(number: int) -> str:
    """Resolve an issue number to its GraphQL node id (in the current repo)."""
    owner, name = _owner_repo()
    q = "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){issue(number:$n){id}}}"
    out = run(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={q}",
            "-f",
            f"o={owner}",
            "-f",
            f"r={name}",
            "-F",
            f"n={number}",
            "--jq",
            ".data.repository.issue.id",
        ]
    )
    if not out:
        raise ValueError(f"Could not resolve node id for issue #{number}")
    return out


_GITHUB_OWNER_RE = re.compile(r"github\.com[:/]([^/]+)/")


def remote_owner(url: str) -> str | None:
    """The owner in a GitHub remote URL (https or ssh), or None for a non-GitHub remote."""
    m = _GITHUB_OWNER_RE.search(url)
    return m.group(1) if m else None


def is_work_repo(owner: str, active_login: str, active_orgs: list[str]) -> bool:
    """A work repo is owned by the active (work) account or by one of its orgs."""
    return owner.lower() in {active_login.lower(), *(o.lower() for o in active_orgs)}


def personal_account(accounts: list[dict]) -> str:
    """The one logged-in gh account that isn't active. Anything else is a setup the rule can't
    resolve, so it aborts instead of guessing which account a push should go out as."""
    others = [a["login"] for a in accounts if not a["active"] and a["state"] == "success"]
    if len(others) != 1:
        raise SystemExit(
            f"This repo isn't owned by the active gh account or one of its orgs, so it needs the personal "
            f"account: the one other logged-in gh account. Found {len(others)}: {others}. "
            f"Fix it with `gh auth login`/`gh auth logout`, or export GH_TOKEN for this run."
        )
    return others[0]


def use_owner_account() -> None:
    """Point every `gh` and `git` call this process makes at the right one of two gh accounts.

    The active account is the work one, and repos owned by it or one of its orgs use it. Every other
    repo uses the personal account, the other logged-in one. Switching accounts is out, since the
    active account is machine-wide and other sessions rely on it; an exported `GH_TOKEN` reaches both
    `gh` and `git push` (through gh's credential helper) for this process only. An explicit
    `GH_TOKEN` already in the environment wins.
    """
    if "GH_TOKEN" in os.environ:
        return
    owner = remote_owner(run(["git", "remote", "get-url", "origin"]))
    if owner is None:
        return
    accounts = json.loads(run(["gh", "auth", "status", "--json", "hosts"]))["hosts"]["github.com"]
    active_login = next(a["login"] for a in accounts if a["active"])
    if owner.lower() != active_login.lower():
        active_orgs = run(["gh", "api", "user/orgs", "--paginate", "--jq", ".[].login"]).splitlines()
        if not is_work_repo(owner, active_login, active_orgs):
            os.environ["GH_TOKEN"] = run(["gh", "auth", "token", "--user", personal_account(accounts)])


def _owner_repo() -> tuple[str, str]:
    out = run(["gh", "repo", "view", "--json", "owner,name", "--jq", "[.owner.login, .name] | @tsv"])
    owner, name = out.split("\t")
    return owner, name


def create_issue(title: str, body: str, labels: list[str]) -> int:
    args = ["gh", "issue", "create", "--title", title, "--body", body]
    for label in labels:
        args += ["--label", label]
    url = run(args)
    # `gh issue create` prints the issue URL; the trailing path segment is the number.
    return int(url.rstrip("/").rsplit("/", 1)[-1])


def open_pr_for_branch(branch: str) -> int | None:
    """Return the number of the open PR whose head is `branch`, or None if there is none.

    Used by `adopt` so wiring a branch that already has a PR links it automatically instead
    of leaving `pr: null` until someone remembers to run `feature pr`. `gh pr list` returns
    an empty array (not an error) when nothing matches, so no result is a real "no PR" — not
    a swallowed failure — and we surface that as None.
    """
    out = run(
        ["gh", "pr", "list", "--head", branch, "--state", "open", "--json", "number", "--jq", ".[0].number // empty"]
    )
    return int(out) if out else None


def link_pr_to_feature(pr: int, issue: int) -> bool:
    """Put a native `Closes #<issue>` reference in the PR body, creating a real GitHub link that
    shows in the PR's sidebar and closes the tracking issue when the PR merges. Returns True if
    the body changed.

    This used to be a deliberately non-closing `Part of #<issue>`, on the theory that the feature
    issue outlives the PR and should be closed by `feature merge` once the gate is clean. In
    practice that theory produced a graveyard: `feature merge` is a step a human has to remember
    *after* clicking merge in the GitHub UI, by which point the PR is gone from view and the branch
    is deleted, so nobody ever ran it — one repo reached 12 open tracking issues whose PRs had
    merged weeks earlier. A closing keyword needs nobody to remember anything.

    Two gaps remain, and `feature reconcile` exists for both: GitHub only honours the keyword when
    the PR merges into the **default branch**, so a stacked feature merged into its parent still
    needs closing by hand, and the auto-close writes no record — the state block stays at
    `in-review` and nothing names the debt that shipped.

    Any stale reference line of ours is replaced, so re-linking (or upgrading an old
    `Part of #…`) never stacks a second one; `prbody` places it in the body's layout.
    """
    body = run(["gh", "pr", "view", str(pr), "--json", "body", "--jq", ".body"])
    if prbody.parse(body).ref == prbody.feature_ref(issue):
        return False  # already linked to this issue
    run(["gh", "pr", "edit", str(pr), "--body-file", "-"], input_text=prbody.with_ref(body, issue))
    return True


def set_pr_assessment(pr: int, text: str) -> None:
    body = run(["gh", "pr", "view", str(pr), "--json", "body", "--jq", ".body"])
    run(["gh", "pr", "edit", str(pr), "--body-file", "-"], input_text=prbody.with_assessment(body, text))


def set_pr_description(pr: int, text: str, title: str | None) -> None:
    body = run(["gh", "pr", "view", str(pr), "--json", "body", "--jq", ".body"])
    cmd = ["gh", "pr", "edit", str(pr), "--body-file", "-"]
    if title:
        cmd += ["--title", title]
    run(cmd, input_text=prbody.with_description(body, text))


def create_draft_pr(*, branch: str, base: str, title: str, body: str, cwd: str) -> str:
    """Open a draft PR for `branch` against `base`; returns its URL."""
    return run(
        ["gh", "pr", "create", "--draft", "--head", branch, "--base", base, "--title", title, "--body-file", "-"],
        cwd=cwd,
        input_text=body,
    )


# How many PRs to ask about per aliased GraphQL query. Well under any complexity limit, and it
# keeps a repo with hundreds of tracked features to a handful of round trips.
_PR_BATCH = 50


def pr_states(numbers: list[int]) -> dict[int, str]:
    """Map PR number → `OPEN` | `MERGED` | `CLOSED`, batched into aliased GraphQL queries.

    Batched because the reconcile sweep asks about every tracked feature at once, and a
    `gh pr view` per PR turns a one-second command into a minute of round trips. A number with no
    such PR makes the whole query fail (GraphQL reports it as an error) rather than reading as an
    absent PR — a state block pointing at a PR that doesn't exist is corruption, not a normal case.
    """
    unique = sorted({int(n) for n in numbers})
    if not unique:
        return {}  # nothing to ask about; don't even resolve the repo
    owner, name = _owner_repo()
    states: dict[int, str] = {}
    for start in range(0, len(unique), _PR_BATCH):
        fields = " ".join(f"p{n}: pullRequest(number:{n}){{number state}}" for n in unique[start : start + _PR_BATCH])
        q = f"query($o:String!,$r:String!){{repository(owner:$o,name:$r){{{fields}}}}}"
        out = run(
            [
                "gh",
                "api",
                "graphql",
                "-f",
                f"query={q}",
                "-f",
                f"o={owner}",
                "-f",
                f"r={name}",
                "--jq",
                ".data.repository",
            ]
        )
        for pr in json.loads(out).values():
            states[pr["number"]] = pr["state"]
    return states


def pr_state(number: int) -> str:
    """`OPEN` | `MERGED` | `CLOSED` for a single PR."""
    return pr_states([number])[number]


def pr_base_branch(number: int) -> str:
    """The base branch a PR targets (`baseRefName`). This defines the PR's review diff
    (`git diff <base>...HEAD`), so the review scope is only correct when it equals the
    feature's tracked parent branch."""
    out = run(["gh", "pr", "view", str(number), "--json", "baseRefName", "--jq", ".baseRefName"])
    if not out:
        raise ValueError(f"Could not resolve base branch for PR #{number}")
    return out


def pr_diff_stats(number: int) -> tuple[int, int]:
    """(changed_files, changed_lines) for a PR — the size half of review-budget sizing."""
    out = run(
        [
            "gh",
            "pr",
            "view",
            str(number),
            "--json",
            "changedFiles,additions,deletions",
            "--jq",
            "[.changedFiles, (.additions + .deletions)] | @tsv",
        ]
    )
    files, lines = out.split("\t")
    return int(files), int(lines)


def pr_changed_paths(number: int, *, expected: int) -> list[str]:
    """Repo-relative paths the PR touches — the sensitivity half of review-budget sizing.

    Scoped by the PR's own base, like every other view of the diff in this workflow. Uses the REST
    files listing, not `gh pr diff --name-only`: the diff endpoint refuses any PR over 300 files
    (HTTP 406), which crashed `status` and `review record` on large PRs. The listing pages up to
    3000 files and then stops without an error, so the count is checked against `expected` (the
    PR's `changedFiles`) — a budget sized on a partial path list would miss sensitive hits.
    """
    owner, name = _owner_repo()
    out = run(
        ["gh", "api", f"repos/{owner}/{name}/pulls/{number}/files?per_page=100", "--paginate", "--jq", ".[].filename"]
    )
    paths = out.splitlines() if out else []
    if len(paths) != expected:
        raise RuntimeError(
            f"PR #{number} reports {expected} changed files but the files listing returned {len(paths)} "
            f"(GitHub caps it at 3000); refusing to size the review budget on a partial path list."
        )
    return paths


def add_label(number: int, label: str) -> None:
    """Add a label to an issue (used to stamp a problem's recorded disposition)."""
    run(["gh", "issue", "edit", str(number), "--add-label", label])


def remove_label(number: int, label: str) -> None:
    """Remove a label from an issue (used to revoke a disposition, so it blocks again)."""
    run(["gh", "issue", "edit", str(number), "--remove-label", label])


def get_issue_body(number: int) -> str:
    return run(["gh", "issue", "view", str(number), "--json", "body", "--jq", ".body"])


def get_issue(number: int) -> dict:
    """{number, title, state, body} for one issue — the same shape `feature_issues` yields, so a
    single-feature reconcile runs through the same code as the repo-wide sweep."""
    return json.loads(run(["gh", "issue", "view", str(number), "--json", "number,title,state,body"]))


def feature_issues() -> list[dict]:
    """[{number, title, state, body}] for every `feature`-labelled issue in the repo.

    Closed ones included: a merged PR now closes its tracking issue through the `Closes` reference,
    and the reconcile sweep still has to find that issue to write the outcome into its state block.
    Paginated for the same reason `sub_issues` is — a repo accumulates features for as long as it
    uses this workflow, and a sweep that silently stops at 100 would leave the oldest ones orphaned
    forever.
    """
    owner, name = _owner_repo()
    q = (
        "query($o:String!,$r:String!,$endCursor:String){repository(owner:$o,name:$r)"
        '{issues(first:50,labels:["feature"],after:$endCursor){'
        "pageInfo{hasNextPage,endCursor} nodes{number title state body}}}}"
    )
    out = run(
        [
            "gh",
            "api",
            "graphql",
            "--paginate",
            "-f",
            f"query={q}",
            "-f",
            f"o={owner}",
            "-f",
            f"r={name}",
            "--jq",
            ".data.repository.issues.nodes",
        ]
    )
    issues: list[dict] = []
    for page in out.splitlines():
        page = page.strip()
        if page:
            issues.extend(json.loads(page))
    return issues


def set_issue_body(number: int, body: str) -> None:
    run(["gh", "issue", "edit", str(number), "--body-file", "-"], input_text=body)


def comment(number: int, text: str) -> None:
    run(["gh", "issue", "comment", str(number), "--body", text])


def close_issue(number: int, comment_text: str, *, not_planned: bool = False) -> None:
    reason = ["--reason", "not planned"] if not_planned else []
    run(["gh", "issue", "close", str(number), "--comment", comment_text, *reason])


def reopen_issue(number: int, comment_text: str) -> None:
    run(["gh", "issue", "reopen", str(number), "--comment", comment_text])


def link_sub_issue(parent: int, child: int) -> None:
    parent_id = _node_id(parent)
    child_id = _node_id(child)
    mutation = "mutation($p:ID!,$c:ID!){addSubIssue(input:{issueId:$p,subIssueId:$c}){issue{number}}}"
    run(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={mutation}",
            "-f",
            f"p={parent_id}",
            "-f",
            f"c={child_id}",
        ]
    )


def sub_issues(parent: int) -> list[dict]:
    """Return [{number, title, state, labels:[...]}] for ALL of a feature's sub-issues.

    Fully paginated — `gh api graphql --paginate` follows the cursor as long as the query
    exposes `pageInfo{hasNextPage,endCursor}` and an `$endCursor` variable. No arbitrary cap:
    the gate open-count and dedup set must see every sub-issue, however many review runs filed.
    """
    owner, name = _owner_repo()
    q = (
        "query($o:String!,$r:String!,$n:Int!,$endCursor:String){repository(owner:$o,name:$r)"
        "{issue(number:$n){subIssues(first:100,after:$endCursor){"
        "pageInfo{hasNextPage,endCursor} nodes{number title state "
        "labels(first:50){nodes{name}}}}}}}"
    )
    # --paginate concatenates each page's JSON document; --jq runs per page, so we get one
    # JSON array of nodes per page. Parse each line and flatten.
    out = run(
        [
            "gh",
            "api",
            "graphql",
            "--paginate",
            "-f",
            f"query={q}",
            "-f",
            f"o={owner}",
            "-f",
            f"r={name}",
            "-F",
            f"n={parent}",
            "--jq",
            ".data.repository.issue.subIssues.nodes",
        ]
    )
    nodes: list[dict] = []
    for page in out.splitlines():
        page = page.strip()
        if page:
            nodes.extend(json.loads(page))
    return [
        {
            "number": n["number"],
            "title": n["title"],
            "state": n["state"],
            "labels": [label["name"] for label in n["labels"]["nodes"]],
        }
        for n in nodes
    ]
