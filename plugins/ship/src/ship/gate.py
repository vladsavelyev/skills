"""The merge gate — pure decision logic, kept free of I/O so it's unit-testable.

The gate has **three** outcomes, because a review loop has three real endings:

  OPEN            — no problem is open, and a review has covered the code that is on the
                    branch right now, or by nothing since that needs one (`needs_review`).
                    Ship it.
  REVIEW_AGAIN    — open problems or unreviewed code are outstanding and there is review budget
                    left to spend.
  NEEDS_DECISION  — the loop is not converging on its own: the budget is spent, or the trend
                    says another round won't help. A human decides whether to buy another round,
                    merge the fixes unreviewed, split the PR, or redesign. When a kind of bug came
                    back after its fix, the gate recommends the redesign. Either way the open
                    problems are fixed first.

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

from . import assess, budget

# What to do next, per verdict — the gate's most useful output for an agent driving the loop.
_NEXT_OPEN = "Human merges the PR, then close it out with `Skill(ship:close)`."
# A review after the merge can block nothing, and the old advice ("review again") sent agents to do
# exactly that on a merged PR whose last commits were never reviewed.
_NEXT_MERGED = "Don't review: the PR has merged. Close the feature out with `Skill(ship:close)`."
# The budget limits review rounds, not fixes. An older wording offered "ship the rest as debt", so
# an agent at a spent budget left real problems open that it could have fixed in minutes. Nothing
# ships as debt: every open problem is fixed, or closed as `wontfix` because it isn't relevant.
_NEXT_DECISION = (
    "STOP reviewing, but still close every open problem (`Skill(ship:fix)`: it fixes each, or closes it "
    "as `wontfix` if it isn't relevant). The budget limits review rounds, not fixes, and nothing "
    "ships as debt. Then `feature escalate --reason '<what you recommend>'`, and a human decides "
    "whether to review the fixes in another round (`feature budget --set <n>`), merge with them "
    "unreviewed, or split/redesign the PR."
)
# A bug back after its fix means the design keeps producing it, so another fix-and-review round
# would likely find the next one: the gate recommends a redesign over another round. The open problems
# still get fixed first. They once waited for the human's call ("Don't fix the open problems yet"), and
# on navari-stack #23 nine sat open while the PR body listed them; the user wants problems fixed as
# they're found, and a redesign that follows can still replace the patches. Why: docs/motivation.md #15.
_NEXT_REDESIGN = (
    "STOP reviewing, but first close every open problem (`Skill(ship:fix)`: it fixes each at its root cause, "
    "or closes it as `wontfix` if it isn't relevant). Then `feature escalate --reason '<what the recurring "
    "bugs have in common>'`: the same kind of bug came back after its fix, so the design keeps producing it, "
    "and another round of review would likely find the next one. Recommend a redesign over another round: a "
    "few competing designs, each critiqued blind by a separate agent, then one settled from the critiques. A "
    "human decides: redesign (replacing the fixes where it must), review the fixes in another round "
    "(`feature budget --set <n>`, which also buys the round that reviews a redesign), or split the PR."
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

    @property
    def settled(self) -> bool:
        """Whether a ship run may end here: the PR is safe to merge, or already in. Anything else
        leaves work in the loop (fixes, a review), and a held session keeps going (`hold.py`)."""
        return self.verdict in (Verdict.OPEN, Verdict.MERGED)

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


def recurrence_reason(rounds: list[dict]) -> str | None:
    """Why the last round says the fixes aren't holding, or None.

    It found again what an earlier fix should have ruled out: the very problem, re-opened (a
    regression), or a new problem of the same kind, which the review filed as repeating the fixed
    one (`problem add --repeats`, kept per round as `repeats`). Either way a fix patched an instance
    and the design still produces the bug, so the gate recommends a redesign over another round.

    Only the last round counts: a human who saw an earlier recurrence and bought more rounds answered
    it. Like the trend, it reads high/med finds only: a nit back again is not evidence the design is
    wrong.
    """
    if not rounds or rounds[-1].get("placeholder"):
        return None
    last = rounds[-1]
    back = []
    if last["regressions"]:
        back.append(f"re-opened {last['regressions']} fixed high/med problem(s)")
    # Rounds recorded before `repeats` existed carry no links. Kept ON PURPOSE: they are already
    # written into feature issues of PRs in flight.
    if repeats := last.get("repeats"):
        pairs = ", ".join(f"#{problem} like #{repeated}" for problem, repeated in repeats.items())
        back.append(f"found {len(repeats)} new high/med problem(s) of a kind already fixed ({pairs})")
    if not back:
        return None
    cause = "the fixes patch instances, not the cause, so redesign rather than review again"
    return f"run {last['run']} {' and '.join(back)}: {cause}"


def rising_reason(rounds: list[dict]) -> str | None:
    """Why the trend says another round won't converge, or None: high/med findings stopped
    decreasing round over round, so the loop is treading water.

    It keys off high/med counts only. Low-severity problems block the gate like any other, but a
    rising count of nits is not evidence that the design is wrong.
    """
    # Placeholder rounds carry no counts (see `state.migrate`), so a trend through one is
    # meaningless — the budget check in `evaluate` is what catches those features.
    if len(rounds) < 2 or any(r.get("placeholder") for r in rounds[-2:]):
        return None
    previous, current = rounds[-2]["new_blocking"], rounds[-1]["new_blocking"]
    # Strictly increasing, and both nonzero. One flat round (1 then 1) is the most common shape of a
    # converging review, and firing on it made the auto-sized 3rd and 4th rounds nearly unreachable —
    # the advertised 2-4 budget was effectively always 2. A rising count is the real "getting worse"
    # signal; a plateau is left to the budget to bound.
    if current and previous and current > previous:
        return f"high/med findings are rising ({previous} then {current}) — another round is unlikely to converge"
    return None


def covers_head(last_review: dict | None, head_sha: str) -> bool:
    """Whether the last valid review looked at exactly the commit that is on the branch now.

    Short and long shas both occur (the CLI hands out `--short`, a caller may pass a full one), so
    compare by prefix in either direction rather than demanding identical strings.
    """
    if last_review is None:
        return False
    reviewed = last_review["sha"]
    return reviewed.startswith(head_sha) or head_sha.startswith(reviewed)


def needs_review(changes: list[tuple[str, list[str]]], *, low_fixes: set[str], sensitive: list[str]) -> list[str]:
    """The commits since the last review that a review still has to see, oldest first.

    `changes` pairs each commit since the reviewed one with the paths of its own change
    (`__main__._own_changes`). A commit needs no review if it fixed only low problems (`low_fixes`;
    a high or med problem's fix may be as wrong as the change it fixed), or if its own change is
    empty or only docs and tests (`assess.path_risk` low). Why: docs/motivation.md #12.
    """
    return [
        sha
        for sha, paths in changes
        if sha not in low_fixes
        and paths
        and assess.path_risk(paths, budget.sensitive_matches(paths, sensitive)) != "low"
    ]


def escalation_refusal(decision: GateDecision, *, open_problems: list[int], named: list[int]) -> str | None:
    """Why `feature escalate` must not hand this feature to a human yet, or None if it may.

    Escalating ends a held ship run (`hold.py`), so it is only for what a human really has to decide:
    whether to review the fixes again once the budget is spent or the loop churns, or to redesign when
    a kind of bug came back after its fix, and a problem that needs the user's call, named with
    `--problem`. Every other open problem is closed first, whatever the gate recommends: the budget
    limits reviews, not fixes, and a redesign that follows can still replace the patches. Everything
    else stays in the loop, or escalating would be the way around fixing.
    """
    if decision.verdict is Verdict.OPEN:
        return "the gate is OPEN: there is nothing to decide. Tell the user it's safe to merge."
    if decision.verdict is Verdict.MERGED:
        return "the PR has merged: close the feature out with `Skill(ship:close)` instead."
    if stray := sorted(set(named) - set(open_problems)):
        return f"{', '.join(f'#{n}' for n in stray)} is not an open problem of this feature."
    if unnamed := sorted(set(open_problems) - set(named)):
        return (
            f"{', '.join(f'#{n}' for n in unnamed)} still open. Fix each (`feature problem resolve <#> --commit "
            f"<sha>`) or close it as not relevant (`feature problem wontfix <#> --reason '<why>'`) first: the "
            f"budget limits reviews, not fixes, and a redesign can still replace a fix. Name with `--problem` "
            f"only a problem that needs the user's call."
        )
    if decision.verdict is Verdict.REVIEW_AGAIN and not named:
        return "review budget remains: review the fixes again (`Skill(ship:ship)`) instead of escalating."
    return None


def evaluate(
    *,
    blocking_open: int,
    last_review: dict | None,
    history: list[dict],
    budget: int,
    unreviewed: list[str],
    budget_answers: int | None = None,
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
    of a serious problem commits code a review must see; a `wontfix` close doesn't, so the existing
    review still stands. Counts remain in `review_history` for the convergence trend.

    `unreviewed` is `needs_review`'s list, ignored when there is no last review.

    `budget_answers` is the run an explicit budget was set after (`feature budget --set`), or None
    when the budget is auto-sized.

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
    elif unreviewed:
        reasons.append(
            f"{len(unreviewed)} commit(s) since the last review at {last_review['sha'][:9]} have never been "
            f"reviewed: {', '.join(sha[:9] for sha in unreviewed)}"
        )

    if pr_merged:
        return GateDecision(Verdict.MERGED, reasons, _NEXT_MERGED, used, budget)
    if not reasons:
        return GateDecision(Verdict.OPEN, [], _NEXT_OPEN, used, budget)

    # A budget raised after a round outranks that round's churn signals. They exist to escalate
    # *early*, so a human who saw that escalation and chose to spend another round has answered it;
    # re-deriving them from the same unchanged history would make `budget --set` a silent no-op. A
    # round recorded after it is new evidence the human hasn't seen, judged on its own: a pinned
    # budget once hid every later recurrence.
    if not (rounds and rounds[-1]["run"] == budget_answers):
        # The recurrence goes first, ahead of a spent budget too: it is the one signal that says what
        # to do instead of another round.
        if recurring := recurrence_reason(rounds):
            return GateDecision(Verdict.NEEDS_DECISION, [*reasons, recurring], _NEXT_REDESIGN, used, budget)
        if rising := rising_reason(rounds):
            return GateDecision(Verdict.NEEDS_DECISION, [*reasons, rising], _NEXT_DECISION, used, budget)
    if used >= budget:
        return GateDecision(
            Verdict.NEEDS_DECISION,
            [*reasons, f"review budget spent ({used}/{budget})"],
            _NEXT_DECISION,
            used,
            budget,
        )
    next_step = (
        "Close every open problem (`Skill(ship:fix)`: it fixes each at its root cause, or closes it as "
        "`wontfix` if it isn't relevant). Then a fresh review round (`Skill(ship:review)`) if the gate "
        "still asks for it."
        if blocking_open > 0
        else "Run a fresh review round (`Skill(ship:review)`)."
    )
    return GateDecision(Verdict.REVIEW_AGAIN, reasons, next_step, used, budget)
