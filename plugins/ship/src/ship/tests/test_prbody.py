"""The PR body layout: prose, a rule, ship's meta, and the signature last whichever writer ran."""

import pytest

from ship import prbody

SIG = "🤖 Claude Opus 5.5 in Claude Code (T3 Code)"
REF = prbody.feature_ref(7)
ASSESS = "<!-- ship:assessment -->\ngrade\n<!-- /ship:assessment -->"
FULL = f"Why we need it.\n\n---\n\n{ASSESS}\n\n{REF}\n\n{SIG}\n"


def test_meta_goes_below_a_rule_and_the_signature_last():
    body = prbody.with_ref(f"Why we need it.\n\n{SIG}\n", 7)
    assert body == f"Why we need it.\n\n---\n\n{REF}\n\n{SIG}\n"
    assert prbody.with_assessment(body, "grade") == FULL


def test_every_writer_is_idempotent_on_the_layout():
    assert prbody.with_assessment(FULL, "grade") == FULL
    assert prbody.with_ref(FULL, 7) == FULL
    assert prbody.compose(prbody.parse(FULL)) == FULL


def test_a_body_in_the_old_layout_is_put_right():
    # Before: the meta was appended after whatever the author wrote, signature included.
    old = f"Why we need it.\n\n{SIG}\n\n{ASSESS}\n\n{REF}\n"
    assert prbody.with_assessment(old, "grade") == FULL


def test_assessment_is_replaced_in_place():
    out = prbody.with_assessment(FULL, "new grade")
    assert "\ngrade\n" not in out
    assert out.count("<!-- ship:assessment -->") == 1
    assert out == FULL.replace("\ngrade\n", "\nnew grade\n")


def test_description_replaces_prose_and_signature_and_keeps_the_meta():
    other = "🤖 Claude Sonnet 5.5 in Claude Code"
    assert prbody.with_description(FULL, f"New motivation.\n\n{other}\n") == FULL.replace(
        "Why we need it.", "New motivation."
    ).replace(SIG, other)


def test_description_without_a_signature_drops_the_old_one():
    # The signature belongs to whoever wrote the prose.
    assert prbody.with_description(FULL, "New motivation.") == f"New motivation.\n\n---\n\n{ASSESS}\n\n{REF}\n"


def test_description_on_a_body_without_our_meta():
    assert prbody.with_description("Old.", "New.") == "New.\n"


def test_no_rule_without_meta_or_without_prose():
    assert prbody.compose(prbody.parse(f"Just prose.\n\n{SIG}")) == f"Just prose.\n\n{SIG}\n"
    assert prbody.with_assessment("", "x") == "<!-- ship:assessment -->\nx\n<!-- /ship:assessment -->\n"
    assert prbody.with_ref(SIG, 7) == f"{REF}\n\n{SIG}\n"


def test_prose_keeps_its_own_rules_and_blank_lines():
    prose = "Intro.\n\n---\n\nMore.\n\n```\na\n\n\nb\n```"
    assert prbody.parse(prbody.with_ref(f"{prose}\n\n{SIG}", 7)).prose == prose


def test_signature_mid_prose_is_left_alone():
    # Only trailing signature paragraphs are the signature; a quoted one in the prose isn't.
    prose = f"Intro.\n\n{SIG}\n\nMore."
    assert prbody.parse(prose) == prbody.Parts(prose=prose, signature="", assessment="", ref="")


# A PR about ship itself can quote ship's own lines in a code block; those are prose, never meta.


def test_a_ref_line_quoted_in_a_fence_stays_in_the_prose():
    prose = "Body says:\n\n```\nCloses #42 <!-- ship -->\n```"
    assert prbody.with_description(FULL, prose) == f"{prose}\n\n---\n\n{ASSESS}\n\n{REF}\n"
    # Even after the real ref, the quoted one isn't taken for it.
    quoted = "~~~\nCloses #42 <!-- ship -->\n~~~"
    assert prbody.parse(f"{REF}\n\n{quoted}\n") == prbody.Parts(prose=quoted, signature="", assessment="", ref=REF)


def test_a_signature_or_rule_quoted_at_the_end_of_a_fence_stays_in_it():
    closed = f"```\nfoo\n\n{SIG}\n```"
    assert prbody.with_ref(closed, 7) == f"{closed}\n\n---\n\n{REF}\n"
    rule = "````\nfoo\n\n---\n````"
    assert prbody.parse(prbody.with_ref(rule, 7)).prose == rule


def test_a_description_ending_in_an_unclosed_fence_is_refused():
    # The meta would land inside the code block, and the `Closes` line would stop working.
    unclosed = f"```\nfoo\n\n{SIG}"
    assert prbody.parse(unclosed).prose == unclosed
    with pytest.raises(ValueError, match="never closed"):
        prbody.with_ref(unclosed, 7)


def test_an_assessment_quoted_in_a_fence_is_not_ours():
    quoted = f"```\n{ASSESS}\n```"
    assert prbody.with_assessment(quoted, "real") == f"{quoted}\n\n---\n\n{ASSESS.replace('grade', 'real')}\n"
