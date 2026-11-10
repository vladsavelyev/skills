"""The PR body layout: prose, ship's meta, and the signature last whichever writer ran."""

import pytest

from ship import prbody

SIG = "🤖 Claude Opus 5.5 in Claude Code (T3 Code)"
REF = prbody.feature_ref(7)
ASSESS = "<!-- ship:assessment -->\n> grade\n<!-- /ship:assessment -->"
FULL = f"Why we need it.\n\n{ASSESS}\n\n{REF}\n\n{SIG}\n"


def test_meta_goes_between_the_prose_and_the_signature():
    body = prbody.with_ref(f"Why we need it.\n\n{SIG}\n", 7)
    assert body == f"Why we need it.\n\n{REF}\n\n{SIG}\n"
    assert prbody.with_assessment(body, "> grade") == FULL


def test_every_writer_is_idempotent_on_the_layout():
    assert prbody.with_assessment(FULL, "> grade") == FULL
    assert prbody.with_ref(FULL, 7) == FULL
    assert prbody.with_description(FULL, f"Why we need it.\n\n{SIG}") == FULL
    assert prbody.compose(prbody.parse(FULL)) == FULL


def test_a_body_in_the_old_layout_is_put_right():
    # Before: the meta was appended after whatever the author wrote, signature included.
    old = f"Why we need it.\n\n{SIG}\n\n{ASSESS}\n\n{REF}\n"
    assert prbody.with_assessment(old, "> grade") == FULL
    assert prbody.with_ref(f"Why we need it.\n\n{SIG}\n\nPart of #7\n", 7) == f"Why we need it.\n\n{REF}\n\n{SIG}\n"


def test_assessment_is_replaced_in_place():
    assert prbody.with_assessment(FULL, "> new grade") == FULL.replace("> grade", "> new grade")


def test_relinking_replaces_the_reference():
    assert prbody.with_ref(FULL, 8) == FULL.replace(REF, prbody.feature_ref(8))


def test_description_replaces_prose_and_signature_and_keeps_the_meta():
    other = "🤖 Claude Sonnet 5.5 in Claude Code"
    expected = FULL.replace("Why we need it.", "New motivation.").replace(SIG, other)
    assert prbody.with_description(FULL, f"New motivation.\n\n{other}\n") == expected


def test_description_without_a_signature_drops_the_old_one():
    # The signature belongs to whoever wrote the prose.
    assert prbody.with_description(FULL, "New motivation.") == f"New motivation.\n\n{ASSESS}\n\n{REF}\n"


def test_description_on_a_body_without_our_meta():
    assert prbody.with_description("Old.", "New.") == "New.\n"


def test_empty_prose_or_meta():
    assert prbody.compose(prbody.parse(f"Just prose.\n\n{SIG}")) == f"Just prose.\n\n{SIG}\n"
    assert prbody.with_assessment("", "x") == "<!-- ship:assessment -->\nx\n<!-- /ship:assessment -->\n"
    assert prbody.with_ref(SIG, 7) == f"{REF}\n\n{SIG}\n"
    assert prbody.with_ref("", 7) == f"{REF}\n"


def test_prose_keeps_its_own_rules_and_blank_lines():
    prose = "Intro.\n\n---\n\nMore.\n\n```\na\n\n\nb\n```"
    assert prbody.parse(prbody.with_ref(f"{prose}\n\n{SIG}", 7)).prose == prose


def test_signature_mid_prose_is_left_alone():
    # Only trailing signature paragraphs are the signature; a quoted one in the prose isn't.
    prose = f"Intro.\n\n{SIG}\n\nMore."
    assert prbody.parse(prose) == prbody.Parts(prose=prose, signature="", assessment="", ref="")


def test_text_below_the_meta_ends_the_tail():
    # The accepted cost of reading only the tail: the old meta stays in the prose, and the next
    # write adds a fresh copy below.
    body = f"Why.\n\n{REF}\n\nA note someone added later."
    assert prbody.with_ref(body, 7) == f"{body}\n\n{REF}\n"


# Ship's own lines quoted anywhere in the prose are prose, never meta. A PR about ship quotes them.


def test_a_part_of_line_in_the_prose_survives_every_writer():  # #41
    body = prbody.with_description(f"Old.\n\n{ASSESS}\n\n{REF}\n", "Fix the parser.\n\nPart of #42")
    assert body == f"Fix the parser.\n\nPart of #42\n\n{ASSESS}\n\n{REF}\n"
    assert prbody.with_assessment(body, "> grade") == body
    inline = "Quotes `<!-- ship:assessment -->x<!-- /ship:assessment -->` inline."
    assert prbody.with_assessment(inline, "> grade") == f"{inline}\n\n{ASSESS}\n"


def test_backtick_inline_code_does_not_block_writes():  # #42
    prose = "```console``` blocks are now supported."
    assert prbody.with_ref(prose, 7) == f"{prose}\n\n{REF}\n"


def test_a_fence_ended_by_its_list_item_does_not_block_writes():  # #43
    prose = "- Reproduction:\n\n  ```text\n  output\n\nThe fix changes the parser."
    assert prbody.with_description(f"Old.\n\n{REF}\n", prose) == f"{prose}\n\n{REF}\n"


def test_markers_quoted_in_a_nested_code_block_stay_in_the_prose():  # #44, #40
    prose = "1. Example:\n\n    ```\n    <!-- ship:assessment -->grade<!-- /ship:assessment -->\n    ```\n\nMore."
    assert prbody.with_ref(f"{prose}\n\n{REF}\n", 7) == f"{prose}\n\n{REF}\n"
    quoted = f"Body says:\n\n```\n{REF}\n```"
    assert prbody.with_description(FULL, quoted) == f"{quoted}\n\n{ASSESS}\n\n{REF}\n"


def test_a_write_that_would_not_read_back_as_written_is_refused():
    # With no ref of ours below it, the description's own last line would be read back as ours
    # and replaced by the next `feature pr`. Refuse now rather than let that rewrite it later.
    with pytest.raises(ValueError, match="different prose, ref"):
        prbody.with_description("Old.", "Fix the parser.\n\nPart of #42")
    # Below a real ref it is plain prose, as in #41.
    assert prbody.with_description(f"Old.\n\n{REF}", "Fix.\n\nPart of #42") == f"Fix.\n\nPart of #42\n\n{REF}\n"


def test_a_signature_quoted_at_the_end_of_a_code_block_stays_in_it():
    closed = f"```\nfoo\n\n{SIG}\n```"
    assert prbody.with_ref(closed, 7) == f"{closed}\n\n{REF}\n"
