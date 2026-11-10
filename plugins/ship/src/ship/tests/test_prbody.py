"""Ship's block in a PR body: only what is between its tags is ever rewritten."""

from ship import prbody

SIG = "🤖 Claude Opus 5.5 in Claude Code (T3 Code)"
QUOTE = "> Feature tracking issue: closes #7"


def _block(quote: str = QUOTE) -> str:
    return f"<!-- ship -->\n{quote}\n<!-- /ship -->"


def test_the_block_goes_above_the_signature_or_at_the_end():
    assert prbody.with_block(f"Why.\n\n{SIG}\n", QUOTE, 7) == f"Why.\n\n{_block()}\n\n{SIG}\n"
    assert prbody.with_block("Why.", QUOTE, 7) == f"Why.\n\n{_block()}\n"
    assert prbody.with_block("", QUOTE, 7) == f"{_block()}\n"


def test_only_the_block_is_rewritten():
    # Everything outside the tags comes back byte for byte, whatever it contains.
    above = "Why.\n\n```\nCloses #7 <!-- ship -->\n```\n\nPart of #7\n\nCloses #42\n\n"
    below = f"\n\nA note added later.\n\n{SIG}"
    body = f"{above}{_block('> old')}{below}"
    assert prbody.with_block(body, QUOTE, 7) == f"{above}{_block()}{below}"


def test_writing_the_same_block_changes_nothing():
    body = f"Why.\n\n{_block()}\n\n{SIG}\n"
    assert prbody.with_block(body, QUOTE, 7) == body


def test_description_is_taken_as_written_with_the_block_below():
    assert prbody.with_description(f"New.\n\n{SIG}", QUOTE) == f"New.\n\n{_block()}\n\n{SIG}\n"
    # Even when it quotes ship's tags or its old reference line (a PR about ship).
    quoted = "Looks like:\n\n```\n<!-- ship -->\nexample\n<!-- /ship -->\nCloses #42 <!-- ship -->\n```"
    body = prbody.with_description(quoted, QUOTE)
    assert body == f"{quoted}\n\n{_block()}\n"
    # Later writes change ship's block, the last one, never the example above it.
    assert prbody.with_block(body, "> new", 7) == f"{quoted}\n\n{_block('> new')}\n"


def test_a_code_block_ending_in_a_robot_line_is_not_a_signature():
    text = "Output:\n\n```\nfoo\n\n🤖 Bot\n```"
    assert prbody.with_description(text, QUOTE) == f"{text}\n\n{_block()}\n"


def test_a_body_from_before_the_block_held_the_reference_is_migrated_once():
    # Old layout: an old-named assessment block, then a marked reference line, often after the
    # signature. The first write drops the old reference lines and replaces the block.
    old = (
        f"Why.\n\n{SIG}\n\n<!-- ship:assessment -->\n**Risk high** · …\n<!-- /ship:assessment -->"
        "\n\nCloses #7 <!-- ship -->\n"
    )
    assert prbody.with_block(old, QUOTE, 7) == f"Why.\n\n{SIG}\n\n{_block()}\n"
    for line in ("Closes #7 <!-- feature-workflow -->", "Part of #7"):
        assert prbody.with_block(f"Why.\n\n{line}\n", QUOTE, 7) == f"Why.\n\n{_block()}\n"
    # Someone else's issue is the author's text.
    assert prbody.with_block("Why.\n\nPart of #42\n", QUOTE, 7) == f"Why.\n\nPart of #42\n\n{_block()}\n"
    example = "Why.\n\n```\nCloses #42 <!-- ship -->\n```"
    assert prbody.with_block(f"{example}\n\nCloses #7 <!-- ship -->\n", QUOTE, 7) == f"{example}\n\n{_block()}\n"


def test_the_legacy_inline_marker_is_not_an_opening_tag():
    # `Closes #7 <!-- ship -->` ends in the same text as the opening tag; only a tag on its own
    # line opens the block.
    old = "Why.\n\nCloses #7 <!-- ship -->\n\n<!-- ship:assessment -->\n> old\n<!-- /ship:assessment -->\n"
    assert prbody.with_block(old, QUOTE, 7) == f"Why.\n\n{_block()}\n"
