"""The merge gate — pure decision logic, kept free of I/O so it's unit-testable.

The gate has **three** outcomes, because a review loop has three real endings:

  OPEN            — no problem is open, and a review has covered the code that is on the
                    branch right now. Ship it.
  REVIEW_AGAIN    — open problems or unreviewed code are outstanding and there is review budget
                    left to spend.
  NEEDS_DECISION  — the loop is not converging on its own: the budget is spent, or the trend
                    says another round won't help. The open problems still get fixed; a human
                    decides whether to buy another round, merge the fixes unreviewed, split the
                    PR, or redesign.

Once the PR has merged none of them applies, so a fourth verdict ends the loop for good:

  MERGED          — the PR is in. There is nothing left to review or fix in it; what remains is
                    closing the feature out. The reasons still say what was outstanding when it
                    merged (an unreviewed tail, open problems), because the close-out must deal
                    with them.

Why three. The old gate demanded a review run with *zero* findings. But the reviewer is built
to report at every altitude — real defects, nits, pre-existing issues in touched files — so a
zero-finding run is an event that never happens, and the loop had no other exit. The gate asks
instead whether every filed problem is closed (fixed, or `wontfix` as not relevant — see
`problems.py`; nothing ships as debt) and whether a review covers HEAD, and when the loop won't
settle it says so out loud instead of asking for one more expensive round forever.
"""

from dataclasses import dataclass
from enum import StrEnum

# What to do next, per verdict — the gate's most useful output for an agent driving the loop.
_NEXT_OPEN = "Human merges the PR, then close it out with `Skill(ship:close)`."
# A review after the merge can block nothing, and the old advice ("review again") sent agents to do
# exactly that on a merged PR whose last commits were never reviewed.
_NEXT_MERGED = "Don't review: the PR has merged. Close the feature out with `Skill(ship:close)`."
# The budget limits review rounds, not fixes. An older wording offered "ship the rest as debt", so
# an agent at a spent budget left real problems open that it could have fixed in minutes. Nothing
# ships as debt: every open problem is fixed, or closed as `wontfix` because it isn't relevant.
_NEXT_DECISION = (
    "STOP reviewing, but still close every open problem: fix it, or `feature problem wontfix <#> "
    "--reason '<why>'` if it isn't relevant. The budget limits review rounds, not fixes, and nothing "
    "ships as debt. Then `feature escalate --reason '<what you recommend>'`, and a human decides "
    "whether to review the fixes in another round (`feature budget --set <n>`), merge with them "
    "unreviewed, or split/redesign the PR."
)


class Verdict(StrEnum):
    OPEN = "OPEN"
    REVIEW_AGAIN = "REVIEW_AGAIN"
    NEEDS_DECISION = "NEEDS_DECISION"
    MERGED = "MERGED"


# Exit codes are the machine interface. Deliberately NOT 1 and 2: those are already spoken for by
# "the command failed" — an uncaught exception and `sys.exit("message")` exit 1, argparse usage
# errors exit 2. If REVIEW_AGAIN were 1, a lost `feature-issue` git-config key or a `gh` 503 would
# read as "spend another review round", and a mistyped flag would read as "escalate to a human".
EXIT_CODES = {Verdict.OPEN: 0, Verdict.REVIEW_AGAIN: 10, Verdict.NEEDS_DECISION: 20, Verdict.MERGED: 30}


@dataclass(frozen=True)
class GateDecision:
    verdict: Verdict
    reasons: list[str]
    next_step: str
    rounds_used: int
    budget: int

    @property
    def open(self) -> bool:
        return self.verdict is Verdict.OPEN

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.verdict]

    def __str__(self) -> str:
        used = f"reviews {self.rounds_used}/{self.budget}"
        if self.open:
            return f"OPEN: no open problems, last review covers HEAD ({used}). Safe to merge."
        if self.verdict is Verdict.MERGED:
            shipped = "; ".join(self.reasons) if self.reasons else "no open problems, last review covered it"
            return f"MERGED: the PR is in, nothing left to review (merged with: {shipped}; {used})"
        return f"{self.verdict}: " + "; ".join([*self.reasons, used])


def rounds_since_invalidation(history: list[dict]) -> list[dict]:
    """The review runs that still describe the current code, newest last.

    `review_history` interleaves review entries (which have a `run`) with invalidation markers
    written by `feature sync` when the base advances (which have an `event`). Everything before
    the newest marker reviewed code that has since changed underneath it, so neither the budget
    nor the convergence trend should count it — a base sync legitimately buys a fresh round.
    """
    runs: list[dict] = []
    for entry in reversed(history):
        if "run" not in entry:
            break
        runs.append(entry)
    return list(reversed(runs))


def churn_reason(rounds: list[dict]) -> str | None:
    """Why spending another review round looks futile, or None if the loop is converging.

    Two signals, both cheap and explainable:
      * the last round re-opened previously-closed *blocking* problems — the fixes are
        reintroducing known bugs, which is a design smell, not a review-more smell;
      * blocking findings stopped decreasing round over round — the loop is treading water.

    Both key off high/med counts only. Low-severity problems block the gate like any other, but a
    re-opened or rising count of nits is not evidence that the design is wrong.
    """
    if not rounds or rounds[-1].get("placeholder"):
        return None
    last = rounds[-1]
    if last["regressions"]:
        return (
            f"run {last['run']} re-opened {last['regressions']} previously-closed high/med "
            f"problem(s) — the fixes are reintroducing known bugs"
        )
    # Placeholder rounds carry no counts (see `state.migrate`), so a trend through one is
    # meaningless — the budget check below is what catches those features.
    if len(rounds) >= 2 and not any(r.get("placeholder") for r in rounds[-2:]):
        previous, current = rounds[-2]["new_blocking"], last["new_blocking"]
        # Strictly increasing, and both nonzero. One flat round (1 then 1) is the most common shape
        # of a converging review, and firing on it made the auto-sized 3rd and 4th rounds nearly
        # unreachable — the advertised 2-4 budget was effectively always 2. A rising count is the
        # real "getting worse" signal; a plateau is left to the budget to bound.
        if current and previous and current > previous:
            return f"high/med findings are rising ({previous} then {current}) — another round is unlikely to converge"
    return None


def covers_head(last_review: dict | None, head_sha: str) -> bool:
    """Whether the last valid review looked at the code that is on the branch right now.

    Short and long shas both occur (the CLI hands out `--short`, a caller may pass a full one), so
    compare by prefix in either direction rather than demanding identical strings.
    """
    if last_review is None:
        return False
    reviewed = last_review["sha"]
    return reviewed.startswith(head_sha) or head_sha.startswith(reviewed)


def evaluate(
    *,
    blocking_open: int,
    last_review: dict | None,
    history: list[dict],
    budget: int,
    head_sha: str,
    budget_is_explicit: bool = False,
    pr_merged: bool = False,
) -> GateDecision:
    """Decide the gate from the open problems and whether a review covers the current code.

    `blocking_open` counts every open problem of the feature, at every severity (see
    `problems.is_blocking`): nothing ships as debt, so any open problem keeps the gate shut.

    Note what the second condition is NOT. It used to be "the last review reported zero new blocking
    problems", which reads a *count* — and a count goes stale the moment a problem is closed without
    a commit: once the agent closed the rest as not relevant, the gate still saw the old nonzero
    count and stayed shut with no way to reopen it. Coverage asks the question the safety property
    actually cares about — *has this code been reviewed?* — and answers both cases correctly: a fix
    commits, so HEAD moves and a fresh review is required; a `wontfix` close doesn't, so the
    existing review still stands. Counts remain in `review_history` for the convergence trend.

    `pr_merged` short-circuits to MERGED but keeps the reasons: the loop is over either way, and the
    close-out record names what was still outstanding when the PR went in.
    """
    rounds = rounds_since_invalidation(history)
    used = len(rounds)

    reasons: list[str] = []
    if blocking_open > 0:
        reasons.append(f"{blocking_open} problem(s) open, every one blocking")
    if last_review is None:
        reasons.append("no valid review run recorded")
    elif not covers_head(last_review, head_sha):
        reasons.append(
            f"the last review looked at {last_review['sha']}, but the branch is now at {head_sha} — "
            f"that code has never been reviewed"
        )

    if pr_merged:
        return GateDecision(Verdict.MERGED, reasons, _NEXT_MERGED, used, budget)
    if not reasons:
        return GateDecision(Verdict.OPEN, [], _NEXT_OPEN, used, budget)

    # An explicitly raised budget outranks the churn heuristic. Churn exists to escalate *early*, so
    # a human who has already seen that escalation and chosen to spend another round has answered it;
    # re-deriving churn from the same unchanged history would make `budget --set` a silent no-op.
    churn = None if (budget_is_explicit and used < budget) else churn_reason(rounds)
    if churn:
        return GateDecision(Verdict.NEEDS_DECISION, [*reasons, churn], _NEXT_DECISION, used, budget)
    if used >= budget:
        return GateDecision(
            Verdict.NEEDS_DECISION,
            [*reasons, f"review budget spent ({used}/{budget})"],
            _NEXT_DECISION,
            used,
            budget,
        )
    next_step = (
        "Close every open problem: fix it, or `feature problem wontfix <#> --reason '<why>'` if it "
        "isn't relevant. Then re-review and `feature review record`."
        if blocking_open > 0
        else "Review the PR and `feature review record` the run."
    )
    return GateDecision(Verdict.REVIEW_AGAIN, reasons, next_step, used, budget)
