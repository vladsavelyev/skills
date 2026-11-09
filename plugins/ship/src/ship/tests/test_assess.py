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
        ("M", "low", "low", "medium"),
        ("S", "low", "med", "medium"),
        ("L", "low", "high", "high"),  # bumps cap at one below high risk
        ("M", "med", "med", "high"),
        ("XL", "med", "high", "xhigh"),
        ("S", "high", "low", "xhigh"),
        ("L", "high", "low", "max"),
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
