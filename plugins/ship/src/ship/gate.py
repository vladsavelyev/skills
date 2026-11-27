"""The merge gate — pure decision logic, kept free of I/O so it's unit-testable.

Before the merge the gate has **four** outcomes:

  OPEN            — no problem is open, and a review has covered the code that is on the
                    branch right now, or by nothing since that needs one (`needs_review`).
                    Ship it.
  REVIEW_AGAIN    — open problems or unreviewed code are outstanding and there is review budget
                    left to spend.
  REDESIGN        — a kind of bug came back after its fix: the design keeps producing it, so the
                    run redesigns instead of patching, closes the problems the redesign fixes, and
                    reviews the result. No human is asked: the run makes the redesign it would
                    otherwise recommend, unless it crosses a one-way door (a public API, stored
                    data, a shared environment).
  NEEDS_DECISION  — the loop is not converging on its own: the budget is spent, the trend says
                    another round won't help, or a redesign already ran and the kind came back
                    anyway. A human decides whether to buy another round, merge the fixes
                    unreviewed, split the PR, or redesign again. The open problems are fixed first.

Once the PR has merged none of them applies, so a fifth verdict ends the loop for good:

  MERGED          — the PR is in. There is nothing left to review or fix in it; what remains is
                    closing the feature out. The reasons still say what was outstanding when it
                    merged (an unreviewed tail, open problems), because the close-out must deal
                    with them.

Why not just OPEN and REVIEW_AGAIN. The old gate demanded a review run with *zero* findings. But the reviewer is built
to report at every altitude — real defects, nits, pre-existing issues in touched files — so a
zero-finding run is an event that never happens, and the loop had no other exit. The gate asks
instead whether every filed problem is closed (fixed, or `wontfix` as not relevant — see
`problems.py`; nothing ships as debt) and whether a review covers HEAD, and when the loop won't
settle it says so out loud instead of asking for one more expensive round forever.
"""

from collections.abc import Sequence
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
# A bug back after its fix means the design keeps producing it, so another fix-and-review round would
# likely find the next one. The run redesigns then, without asking. It once escalated with the fixes held
# back ("Don't fix the open problems yet"), then with them made first; on navari-stack #23 nine problems
# sat open while the run waited for a yes to the redesign it recommended, and the user's answer was that a
# redesign the run believes in is its to make. Why: docs/motivation.md #15.
_NEXT_REDESIGN = (
    "Redesign, don't patch, and don't stop to ask: the same kind of bug came back after its fix, so the design "
    "keeps producing it, and another round of fixes and review would likely find the next one. A fix round that "
    "redesigns (`Skill(ship:fix)` with `redesign: <the cause the recurring bugs share>`): a few competing "
    "designs, each critiqued blind by a separate agent, then the one settled from the critiques, built. It "
    "closes each open problem the redesign fixes with the redesign's commits, and fixes the rest at the root. "
    "Only a design that crosses a one-way door (a public API, stored data, a shared environment) comes back "
    "as a `Question:` before it is built, its problems left open for the user's call. "
    "Then `feature redesign --reason '<the cause, and the design settled on>'`, which buys the round that "
    "reviews it, and review again."
)
# The one recurrence a human sees: the run redesigned once and the kind of bug came back anyway, so the
# run's own diagnosis was wrong and a second try on its say-so would be a loop.
_NEXT_REDESIGN_FAILED = (
    "STOP reviewing, but first close every open problem (`Skill(ship:fix)`: it fixes each at its root cause, "
    "or closes it as `wontfix` if it isn't relevant). The run already redesigned, and the round that reviewed "
    "the redesign found the same kind of bug again, so its diagnosis of the cause missed. Then `feature "
    "escalate --reason '<what the redesign missed, and what you recommend>'`. A human decides: redesign again, "
    "review the fixes in another round (`feature budget --set <n>` buys the round either way), or split the PR."
)


class Verdict(StrEnum):
    OPEN = "OPEN"
    REVIEW_AGAIN = "REVIEW_AGAIN"
    REDESIGN = "REDESIGN"
    NEEDS_DECISION = "NEEDS_DECISION"
    MERGED = "MERGED"


# Exit codes are the machine interface. Deliberately NOT 1 and 2: those are already spoken for by
# "the command failed" — an uncaught exception and `sys.exit("message")` exit 1, argparse usage
# errors exit 2. If REVIEW_AGAIN were 1, a lost `feature-issue` git-config key or a `gh` 503 would
# read as "spend another review round", and a mistyped flag would read as "escalate to a human".
EXIT_CODES = {
    Verdict.OPEN: 0,
    Verdict.REVIEW_AGAIN: 10,
    Verdict.NEEDS_DECISION: 20,
    Verdict.MERGED: 30,
    Verdict.REDESIGN: 40,
}


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


def _links(history: list[dict]) -> dict[int, int]:
    """Every `--repeats` link the recorded rounds of `history` keep: a problem -> the fixed one it repeats."""
    return {int(problem): repeated for entry in history for problem, repeated in entry.get("repeats", {}).items()}


def chain_root(problem: int, history: list[dict]) -> int:
    """The first problem of `problem`'s recurrence chain: the one it repeats, and the one that one repeats,
    back to a problem that repeats none. A link always points at an older issue, fixed when the newer one
    was filed (`problem add --repeats`), so the walk ends."""
    links = _links(history)
    while problem in links:
        problem = links[problem]
    return problem


def recurrences(entry: dict, *, dismissed: frozenset[int]) -> dict[int, int]:
    """What round `entry` found again of a kind an earlier fix should have ruled out, each find with the
    problem it brings back: a fixed problem re-opened (a regression, `regressed`, which brings back itself),
    or a new one filed as repeating a fixed one (`problem add --repeats`, kept as `repeats`). A find since
    closed as `wontfix` (`dismissed`) is no recurrence: it wasn't a bug.

    Like the trend, it reads high/med finds only: a nit back again is not evidence the design is wrong.
    Rounds recorded before `repeats` or `regressed` existed name no find, so nothing recurs in them:
    kept ON PURPOSE, as they are written into feature issues of PRs in flight.
    """
    back = {int(problem): repeated for problem, repeated in entry.get("repeats", {}).items()}
    back |= {problem: problem for problem in entry.get("regressed", [])}
    return {problem: back[problem] for problem in sorted(back) if problem not in dismissed}


def _recurrence_reason(entry: dict, back: dict[int, int]) -> str:
    """Why round `entry`, which brought `back` (`recurrences`), says the fixes aren't holding: a fix
    patched an instance and the design still produces the bug."""
    regressed = [problem for problem, repeated in back.items() if problem == repeated]
    repeats = {problem: repeated for problem, repeated in back.items() if problem != repeated}
    said = []
    if regressed:
        said.append(f"re-opened {len(regressed)} fixed high/med problem(s) ({', '.join(f'#{n}' for n in regressed)})")
    if repeats:
        pairs = ", ".join(f"#{problem} like #{repeated}" for problem, repeated in repeats.items())
        said.append(f"found {len(repeats)} new high/med problem(s) of a kind already fixed ({pairs})")
    cause = "the fixes patch instances, not the cause, so redesign rather than review again"
    return f"run {entry['run']} {' and '.join(said)}: {cause}"


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
    whether to review the fixes again once the budget is spent, the loop churns or a redesign didn't
    hold, and a problem that needs the user's call, named with `--problem`. Every other open problem is
    closed first: the budget limits reviews, not fixes. A redesign is the run's to make, not the user's,
    so a gate asking for one is no reason to escalate. Everything else stays in the loop, or escalating
    would be the way around fixing.
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
            f"budget limits reviews, not fixes. Name with `--problem` only a problem that needs the user's call."
        )
    if decision.verdict is Verdict.REDESIGN and not named:
        return f"the redesign is yours to make, not the user's: {_NEXT_REDESIGN}"
    if decision.verdict is Verdict.REVIEW_AGAIN and not named:
        return "review budget remains: review the fixes again (`Skill(ship:ship)`) instead of escalating."
    return None


def redesign_refusal(decision: GateDecision, *, open_problems: list[int], commits: list[str]) -> str | None:
    """Why `feature redesign` must not record a redesign yet, or None if it may.

    Recording it buys the round that reviews it, so it needs a recurrence to answer, every problem
    closed (by the redesign, or fixed or `wontfix` beside it), and `commits` since the last review,
    which are the redesign.
    """
    if decision.verdict is not Verdict.REDESIGN:
        return f"the gate is {decision.verdict}, not REDESIGN: no recurrence is waiting for a redesign."
    if open_problems:
        return (
            f"{', '.join(f'#{n}' for n in sorted(open_problems))} still open. Close each the redesign fixes "
            f"with its commits (`feature problem resolve <#> --commit <sha>…`), and fix or `wontfix` the rest."
        )
    if not commits:
        return "no commit since the last review, so nothing was redesigned. Commit and push the redesign first."
    return None


def redesign_chains(history: list[dict], *, dismissed: frozenset[int]) -> list[int]:
    """The recurrence chains (`chain_root`) the last round of `history` brought back, which a redesign
    answering it covers."""
    rounds = rounds_since_invalidation(history)
    back = recurrences(rounds[-1], dismissed=dismissed) if rounds else {}
    return sorted({chain_root(repeated, history) for repeated in back.values()})


def evaluate(
    *,
    blocking_open: int,
    last_review: dict | None,
    history: list[dict],
    budget: int,
    unreviewed: list[str],
    budget_answers: int | None = None,
    redesigns: Sequence[dict] = (),
    dismissed: frozenset[int] = frozenset(),
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

    `redesigns` are the run's redesigns (`state.redesigns`): each the run it answered and the
    recurrence chains it covered. A recurrence stays REDESIGN, even with every problem closed and
    nothing left for `needs_review`, until a redesign answers its round: the redesign may be all docs.
    Then the round that reviews it is due whatever it touched. A chain back after its redesign is one
    the redesign didn't stop, which goes to a human; another chain gets its own redesign.

    `dismissed` are the problems closed as `wontfix`: a recurrence among them is none.

    `pr_merged` short-circuits to MERGED but keeps the reasons: the loop is over either way, and the
    close-out record names what was still outstanding when the PR went in.
    """
    rounds = rounds_since_invalidation(history)
    used = len(rounds)
    last = rounds[-1] if rounds else None
    redesigned = {r["run"] for r in redesigns}

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
    if last is not None and last["run"] in redesigned:
        reasons.append(f"the redesign after run {last['run']} has not been reviewed")

    # A budget raised, or a redesign made, after a round outranks that round's churn signals: they
    # answered it, and re-deriving the signals from the same unchanged history would make `budget
    # --set` a silent no-op and send the run to redesign again what it just redesigned. A round
    # recorded after it is new evidence, judged on its own: a pinned budget once hid every later
    # recurrence.
    judged = last is not None and last["run"] not in {budget_answers, *redesigned}
    back = recurrences(last, dismissed=dismissed) if judged else {}

    if pr_merged:
        return GateDecision(Verdict.MERGED, reasons, _NEXT_MERGED, used, budget)
    if not reasons and not back:
        return GateDecision(Verdict.OPEN, [], _NEXT_OPEN, used, budget)
    # The recurrence goes first, ahead of a spent budget too: it is the one signal that says what to do
    # instead of another round.
    if back:
        recurring = _recurrence_reason(last, back)
        chains = {chain_root(repeated, history) for repeated in back.values()}
        if missed := [r["run"] for r in redesigns if r["run"] < last["run"] and chains & set(r["chains"])]:
            again = f"{recurring}, though the run already redesigned for that kind after run {missed[-1]}"
            return GateDecision(Verdict.NEEDS_DECISION, [*reasons, again], _NEXT_REDESIGN_FAILED, used, budget)
        return GateDecision(Verdict.REDESIGN, [*reasons, recurring], _NEXT_REDESIGN, used, budget)
    if judged and (rising := rising_reason(rounds)):
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
