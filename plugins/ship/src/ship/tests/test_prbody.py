"""Ship's block in a PR body: only what is between its tags is ever rewritten."""

from ship import prbody

SIG = "🤖 Claude Opus 5.5 in Claude Code (T3 Code)"


def _block(*inner: str) -> str:
    return "<!-- ship:assessment -->\n" + "\n\n".join(inner) + "\n<!-- /ship:assessment -->"


def test_the_block_goes_above_the_signature():
    body = prbody.with_ref(f"Why we need it.\n\n{SIG}\n", 7)
    assert body == f"Why we need it.\n\n{_block('Closes #7')}\n\n{SIG}\n"
    assert prbody.with_assessment(body, "> grade") == f"Why we need it.\n\n{_block('> grade', 'Closes #7')}\n\n{SIG}\n"


def test_the_block_goes_at_the_end_without_a_signature():
    assert prbody.with_assessment("Why.", "> grade") == f"Why.\n\n{_block('> grade')}\n"
    assert prbody.with_ref("", 7) == f"{_block('Closes #7')}\n"


def test_only_the_block_is_rewritten():
    # Everything outside the tags comes back byte for byte, whatever it contains.
    outside_above = "Why.\n\n```\nCloses #42 <!-- ship -->\n```\n\nPart of #42\n\n"
    outside_below = f"\n\nA note added later.\n\n{SIG}"
    body = f"{outside_above}{_block('> old', 'Closes #7')}{outside_below}"
    assert prbody.with_assessment(body, "> new") == f"{outside_above}{_block('> new', 'Closes #7')}{outside_below}"
    assert prbody.with_ref(body, 8) == f"{outside_above}{_block('> old', 'Closes #8')}{outside_below}"


def test_writes_are_idempotent():
    body = f"Why.\n\n{_block('> grade', 'Closes #7')}\n\n{SIG}\n"
    assert prbody.with_ref(body, 7) == body
    assert prbody.with_assessment(body, "> grade") == body


def test_description_replaces_the_text_and_keeps_the_block():
    old = f"Old.\n\n{_block('> grade', 'Closes #7')}\n"
    assert prbody.with_description(old, f"New.\n\n{SIG}") == f"New.\n\n{_block('> grade', 'Closes #7')}\n\n{SIG}\n"
    assert prbody.with_description("Old.", "New.") == "New."
    # The new text is taken as written, even when it quotes ship's old reference line.
    quoted = "Bodies used to end with:\n\n```\nCloses #42 <!-- ship -->\n```"
    assert prbody.with_description(old, quoted) == f"{quoted}\n\n{_block('> grade', 'Closes #7')}\n"


def test_a_legacy_reference_line_moves_into_the_block():
    # Bodies written before the block held the reference: a marked line, often after the signature.
    old = f"Why.\n\n{SIG}\n\n{_block('**Risk high** · …')}\n\nCloses #7 <!-- ship -->\n"
    assert prbody.with_assessment(old, "> grade") == f"Why.\n\n{SIG}\n\n{_block('> grade', 'Closes #7')}\n"
    assert prbody.with_ref("Why.\n\nCloses #3 <!-- feature-workflow -->", 7) == f"Why.\n\n{_block('Closes #7')}\n"


def test_a_legacy_part_of_line_for_this_issue_moves_into_the_block():
    assert prbody.with_ref("Why.\n\nPart of #7\n", 7) == f"Why.\n\n{_block('Closes #7')}\n"
    # Someone else's issue is the author's text.
    assert prbody.with_ref("Why.\n\nPart of #42\n", 7) == f"Why.\n\nPart of #42\n\n{_block('Closes #7')}\n"
