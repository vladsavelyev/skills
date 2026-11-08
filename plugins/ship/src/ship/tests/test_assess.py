import pytest

from ship import assess, github


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


def test_assessment_goes_above_the_feature_reference():
    body = "Why we need it.\n\nCloses #7 <!-- ship -->\n"
    out = github.with_assessment(body, "depth high")
    assert (
        out
        == "Why we need it.\n\n<!-- ship:assessment -->\ndepth high\n<!-- /ship:assessment -->\n\nCloses #7 <!-- ship -->\n"
    )


def test_assessment_is_replaced_in_place():
    body = github.with_assessment("Motivation.\n", "old")
    out = github.with_assessment(body + "\nCloses #3 <!-- ship -->\n", "new")
    assert "old" not in out
    assert out.count("<!-- ship:assessment -->") == 1
    assert out.index("new") < out.index("Closes #3")


def test_assessment_on_empty_body():
    assert github.with_assessment("", "x") == "<!-- ship:assessment -->\nx\n<!-- /ship:assessment -->\n"
