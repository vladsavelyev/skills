import pytest

from ship import assess


@pytest.mark.parametrize(
    ("files", "lines", "size"),
    [
        (1, 10, "S"),
        (5, 100, "S"),
        (6, 50, "M"),
        (3, 101, "M"),
        (15, 500, "M"),
        (16, 20, "L"),
        (40, 1500, "L"),
        (41, 10, "XL"),
        (2, 1501, "XL"),
    ],
)
def test_size_bucket(files, lines, size):
    assert assess.size_bucket(files, lines) == size


def test_path_risk_high_on_a_sensitive_hit():
    assert assess.path_risk(["README.md", "src/auth/x.py"], ["src/auth/x.py"]) == "high"


def test_path_risk_low_for_docs_and_tests_only():
    assert assess.path_risk(["README.md", "docs/a/b.md", "src/pkg/tests/test_x.py", "web/a.spec.ts"], []) == "low"


def test_path_risk_med_for_code():
    assert assess.path_risk(["README.md", "src/pkg/core.py"], []) == "med"


def test_last_grade_skips_ungraded_entries():
    g = {"risk": "high", "complexity": "low", "why": "w"}
    history = [
        {"run": 1, "grade": {"risk": "low", "complexity": "low", "why": "x"}},
        {"run": 2, "grade": g},
        {"event": "base-advanced"},
        {"run": 3},
    ]
    assert assess.last_grade(history) == g
    assert assess.last_grade([{"run": 1}]) is None


def _review(grade: dict, history: list[dict], **kw) -> assess.Review:
    defaults = {
        "size": "M",
        "changed_files": 8,
        "changed_lines": 412,
        "open_problems": frozenset(),
    }
    return assess.Review(grade=grade, history=history, **(defaults | kw))


def test_render_quotes_the_grade_the_reference_and_one_line_per_round():
    grade = {
        "risk": "high",
        "door": "one-way",
        "blast_radius": " every open PR's body ",
        "complexity": "med",
        "why": " whole-body rewrites can corrupt the ref ",
    }
    history = [
        {"run": 1, "found": {"low": [40]}, "regressions": 0},
        {"event": "base-advanced"},
        {"run": 2, "found": {"high": [41], "med": [42, 44]}, "regressions": 1},
        # Feature issues recorded before every round ran at one effort carry the round's `depth`.
        {"run": 3, "depth": "max", "found": {}, "new_blocking": 0, "regressions": 0},
    ]
    assert assess.render(issue=39, review=_review(grade, history)) == (
        "> **Risk: high**, one-way door: whole-body rewrites can corrupt the ref\n"
        "> **Blast radius:** every open PR's body\n"
        "> **Complexity:** medium, size M (8 files, 412 lines)\n"
        "> **Tracking issue:** closes #39\n"
        "> **Review round 1**. Findings: 1 low (#40).\n"
        "> **Review round 2**. Findings: 1 high (#41), 2 med (#42, #44), 1 of them back after a fix.\n"
        "> **Review round 3**. Findings: none."
    )


def test_render_keeps_a_grade_recorded_before_the_door_and_blast_radius():
    grade = {"risk": "med", "complexity": "low", "why": "refine answers 429"}
    out = assess.render(issue=39, review=_review(grade, [{"run": 1, "found": {}, "new_blocking": 0}]))
    assert out.splitlines()[:3] == [
        "> **Risk: medium**: refine answers 429",
        "> **Complexity:** low, size M (8 files, 412 lines)",
        "> **Tracking issue:** closes #39",
    ]


def test_render_before_any_review_is_the_reference_alone():
    # `closes` is what makes GitHub close the issue on merge.
    assert assess.render(issue=39, review=None) == "> **Tracking issue:** closes #39"


def test_render_lists_a_hand_given_severity_after_the_known_ones():
    grade = {"risk": "low", "complexity": "low", "why": "docs"}
    history = [{"run": 1, "found": {"critical": [9], "low": [8]}, "new_blocking": 1, "regressions": 0}]
    out = assess.render(issue=39, review=_review(grade, history))
    assert out.splitlines()[-1] == "> **Review round 1**. Findings: 1 low (#8), 1 critical (#9)."


def test_render_shows_rounds_recorded_before_found_existed():
    grade = {"risk": "low", "complexity": "low", "why": "docs"}
    history = [
        {"run": 1, "placeholder": True},
        {"run": 2, "new_blocking": 2, "new_low": 1, "regressions": 0},
        {"run": 3, "new_blocking": 0, "new_low": 0, "regressions": 0},
    ]
    out = assess.render(issue=39, review=_review(grade, history))
    assert out.splitlines()[-3:] == [
        "> **Review round 1**. Not recorded.",
        "> **Review round 2**. Findings: 2 blocking, 1 low.",
        "> **Review round 3**. Findings: none.",
    ]


_GRADE = {"risk": "high", "door": "two-way", "blast_radius": "this repo", "complexity": "med", "why": "a parser"}
# navari-stack #23: round 1's finds are fixed, round 2 filed #36 (med) and #38 (low).
_ROUNDS = [
    {"run": 1, "found": {"med": [30]}, "new_blocking": 1, "regressions": 0},
    {"run": 2, "found": {"med": [36], "low": [38]}, "new_blocking": 1, "regressions": 0},
]


def test_a_rounds_findings_line_waits_until_every_problem_it_filed_is_closed():
    """The user wants a round's problems fixed before its line appears."""
    out = assess.render(issue=35, review=_review(_GRADE, _ROUNDS, open_problems=frozenset({38, 99})))
    assert out.splitlines()[3:] == [
        "> **Tracking issue:** closes #35",
        "> **Review round 1**. Findings: 1 med (#30).",
    ]
    out = assess.render(issue=35, review=_review(_GRADE, _ROUNDS, open_problems=frozenset({99})))
    assert out.splitlines()[-1] == ("> **Review round 2**. Findings: 1 med (#36), 1 low (#38).")


def test_a_reopened_problem_takes_its_rounds_line_down_again():
    out = assess.render(issue=35, review=_review(_GRADE, _ROUNDS, open_problems=frozenset({30})))
    assert out.splitlines()[3:] == [
        "> **Tracking issue:** closes #35",
        "> **Review round 2**. Findings: 1 med (#36), 1 low (#38).",
    ]
