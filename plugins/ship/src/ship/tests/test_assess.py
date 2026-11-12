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


@pytest.mark.parametrize(
    ("size", "risk", "complexity", "depth"),
    [
        ("S", "low", "low", "low"),
        ("L", "low", "med", "low"),  # PR #38: size never lifts a low-risk change
        ("XL", "low", "high", "low"),
        ("M", "med", "med", "medium"),
        ("L", "med", "low", "high"),
        ("XL", "med", "high", "high"),  # bumps cap at one below high risk
        ("S", "high", "low", "high"),
        ("M", "high", "high", "xhigh"),
        ("L", "high", "low", "xhigh"),
        ("XL", "high", "high", "max"),
    ],
)
def test_review_depth(size, risk, complexity, depth):
    assert assess.review_depth(size=size, risk=risk, complexity=complexity) == depth


def test_review_depth_rejects_unknown_values():
    with pytest.raises(ValueError):
        assess.review_depth(size="M", risk="medium", complexity="low")


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
    defaults = {"size": "M", "changed_files": 8, "changed_lines": 412, "next_depth": "max"}
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
        {"run": 1, "depth": "high", "found": {"low": [40]}, "regressions": 0},
        {"event": "base-advanced"},
        {"run": 2, "depth": "xhigh", "found": {"high": [41], "med": [42, 44]}, "regressions": 1},
        {"run": 3, "depth": "xhigh", "found": {}, "regressions": 0},
    ]
    assert assess.render(issue=39, review=_review(grade, history)) == (
        "> **Risk: high**, one-way door: whole-body rewrites can corrupt the ref\n"
        "> Blast radius: every open PR's body\n"
        "> Complexity: medium, size: M (8 files, 412 lines)\n"
        "> Feature tracking issue: closes #39\n"
        "> **Review round 1** (high). Findings: 1 low (#40).\n"
        "> **Review round 2** (xhigh). Findings: 1 high (#41), 2 med (#42, #44), 1 of them back after a fix.\n"
        "> **Review round 3** (xhigh). Findings: none. A re-review after new commits would run at max."
    )


def test_render_keeps_a_grade_recorded_before_the_door_and_blast_radius():
    grade = {"risk": "med", "complexity": "low", "why": "refine answers 429"}
    out = assess.render(issue=39, review=_review(grade, [{"run": 1, "depth": "medium", "found": {}}]))
    assert out.splitlines()[:2] == [
        "> **Risk: medium**: refine answers 429",
        "> Complexity: low, size: M (8 files, 412 lines)",
    ]


def test_render_before_any_review_is_the_reference_alone():
    # `closes` is what makes GitHub close the issue on merge.
    assert assess.render(issue=39, review=None) == "> Feature tracking issue: closes #39"


def test_render_lists_a_hand_given_severity_after_the_known_ones():
    grade = {"risk": "low", "complexity": "low", "why": "docs"}
    history = [{"run": 1, "depth": "low", "found": {"critical": [9], "low": [8]}, "regressions": 0}]
    out = assess.render(issue=39, review=_review(grade, history, next_depth="low"))
    assert (
        out.splitlines()[-1]
        == "> **Review round 1** (low). Findings: 1 low (#8), 1 critical (#9). Next round would run at low."
    )


def test_render_shows_rounds_recorded_before_found_existed():
    grade = {"risk": "low", "complexity": "low", "why": "docs"}
    history = [
        {"run": 1, "placeholder": True},
        {"run": 2, "new_blocking": 2, "new_low": 1, "regressions": 0},
        {"run": 3, "depth": "low", "new_blocking": 0, "new_low": 0, "regressions": 0},
    ]
    out = assess.render(issue=39, review=_review(grade, history, next_depth="low"))
    assert out.splitlines()[-3:] == [
        "> **Review round 1**. Not recorded.",
        "> **Review round 2**. Findings: 2 blocking, 1 low.",
        "> **Review round 3** (low). Findings: none. A re-review after new commits would run at low.",
    ]
