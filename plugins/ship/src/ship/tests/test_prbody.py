"""Ship's block in a PR body: only what is between its tags is ever rewritten."""

import pytest

from ship import prbody

SIG = "🤖 Claude Opus 5.5 in Claude Code (T3 Code)"
CONTENT = "> Feature tracking issue: closes #7"


def _block(content: str = CONTENT) -> str:
    return f"<!-- ship -->\n{content}\n<!-- /ship -->"


def test_the_block_goes_above_the_signature_or_at_the_end():
    assert prbody.with_block(f"Why.\n\n{SIG}\n", CONTENT, 7) == f"Why.\n\n{_block()}\n\n{SIG}\n"
    assert prbody.with_block("Why.", CONTENT, 7) == f"Why.\n\n{_block()}\n"
    assert prbody.with_block("", CONTENT, 7) == f"{_block()}\n"


def test_only_the_block_is_rewritten():
    # Everything outside the tags comes back byte for byte, whatever it contains.
    above = "Why.\n\n```\nCloses #7 <!-- ship -->\n```\n\nPart of #7\n\nCloses #42\n\n"
    below = f"\n\nA note added later.\n\n{SIG}"
    body = f"{above}{_block('> old')}{below}"
    assert prbody.with_block(body, CONTENT, 7) == f"{above}{_block()}{below}"


def test_writing_the_same_block_changes_nothing():
    body = f"Why.\n\n{_block()}\n\n{SIG}\n"
    assert prbody.with_block(body, CONTENT, 7) == body


def test_description_gets_the_block_below_it():
    assert prbody.with_description(f"New.\n\n{SIG}", CONTENT) == f"New.\n\n{_block()}\n\n{SIG}\n"
    assert prbody.with_description("New.", CONTENT) == f"New.\n\n{_block()}\n"


def test_a_description_passed_back_with_its_block_keeps_one():
    # The round-trip that produced two blocks on navari#4727: fetch the body, add a paragraph, pass
    # the whole body back. The old block is dropped and the fresh one placed above the signature.
    fetched = f"Why.\n\n{_block('> old')}\n\n{SIG}\n"
    edited = fetched.replace("Why.", "Why.\n\nA paragraph added later.")
    assert prbody.with_description(edited, CONTENT) == f"Why.\n\nA paragraph added later.\n\n{_block()}\n\n{SIG}\n"
    # Without a signature, and with the block in a different place, or twice.
    assert prbody.with_description(f"Why.\n\n{_block('> old')}\n\nMore.", CONTENT) == f"Why.\n\nMore.\n\n{_block()}\n"
    twice = f"{_block('> a')}\n\nWhy.\n\n{_block('> b')}\n\n{SIG}"
    assert prbody.with_description(twice, CONTENT) == f"Why.\n\n{_block()}\n\n{SIG}\n"
    # Under the legacy name too.
    legacy = "Why.\n\n<!-- ship:assessment -->\n> old\n<!-- /ship:assessment -->\n"
    assert prbody.with_description(legacy, CONTENT) == f"Why.\n\n{_block()}\n"


def test_several_blocks_merge_into_one_at_the_last_ones_place():
    body = f"{_block('> a')}\n\nWhy.\n\n{_block('> b')}\n\nMore.\n\n{_block('> c')}\n\nNote.\n\n{SIG}\n"
    assert prbody.with_block(body, CONTENT, 7) == f"Why.\n\nMore.\n\n{_block()}\n\nNote.\n\n{SIG}\n"
    # Merging changes the body even when the last block already holds `content`, so
    # `set_pr_block` writes it (`feature reconcile` heals such PRs).
    dup = f"Why.\n\n{_block()}\n\n{_block()}\n\n{SIG}\n"
    assert prbody.with_block(dup, CONTENT, 7) == f"Why.\n\n{_block()}\n\n{SIG}\n"


def test_a_body_edited_on_the_web_with_crlf_line_ends_keeps_its_block():
    body = f"Why.\r\n\r\n<!-- ship -->\r\n> old\r\n<!-- /ship -->\r\n\r\n{SIG}"
    assert prbody.with_block(body, CONTENT, 7) == f"Why.\r\n\r\n{_block()}\r\n\r\n{SIG}"


def test_tags_that_dont_pair_up_are_refused():
    # A stray opening tag above the block would pair with the block's closing tag, cutting the
    # text in between, so the body is refused instead. In either direction, through either writer.
    stray = f"Why.\n\n<!-- ship -->\nhalf an example\n\nMore.\n\n{_block('> old')}\n\n{SIG}"
    for text in (stray, "Why.\n\n<!-- ship -->\nhalf an example", f"Why.\n\n<!-- /ship -->\n\n{_block()}"):
        with pytest.raises(ValueError, match="don't pair up"):
            prbody.with_description(text, CONTENT)
        with pytest.raises(ValueError, match="don't pair up"):
            prbody.with_block(text, CONTENT, 7)


def test_a_written_body_without_exactly_one_block_is_refused():
    # Unreachable from content `assess.render` makes; content holding tag lines forces it.
    with pytest.raises(ValueError, match="2 ship blocks"):
        prbody.with_description("Why.", "> a\n<!-- /ship -->\n<!-- ship -->\n> b")


def test_a_code_block_ending_in_a_robot_line_is_not_a_signature():
    text = "Output:\n\n```\nfoo\n\n🤖 Bot\n```"
    assert prbody.with_description(text, CONTENT) == f"{text}\n\n{_block()}\n"


def test_a_body_from_before_the_block_held_the_reference_is_migrated_once():
    # Old layout: an old-named assessment block, then a marked reference line, often after the
    # signature. The first write drops the old reference lines and replaces the block.
    old = (
        f"Why.\n\n{SIG}\n\n<!-- ship:assessment -->\n**Risk high** · …\n<!-- /ship:assessment -->"
        "\n\nCloses #7 <!-- ship -->\n"
    )
    assert prbody.with_block(old, CONTENT, 7) == f"Why.\n\n{SIG}\n\n{_block()}\n"
    for line in ("Closes #7 <!-- feature-workflow -->", "Part of #7"):
        assert prbody.with_block(f"Why.\n\n{line}\n", CONTENT, 7) == f"Why.\n\n{_block()}\n"
    # Someone else's issue is the author's text.
    assert prbody.with_block("Why.\n\nPart of #42\n", CONTENT, 7) == f"Why.\n\nPart of #42\n\n{_block()}\n"
    example = "Why.\n\n```\nCloses #42 <!-- ship -->\n```"
    assert prbody.with_block(f"{example}\n\nCloses #7 <!-- ship -->\n", CONTENT, 7) == f"{example}\n\n{_block()}\n"


def test_the_legacy_inline_marker_is_not_an_opening_tag():
    # `Closes #7 <!-- ship -->` ends in the same text as the opening tag; only a tag on its own
    # line opens the block.
    old = "Why.\n\nCloses #7 <!-- ship -->\n\n<!-- ship:assessment -->\n> old\n<!-- /ship:assessment -->\n"
    assert prbody.with_block(old, CONTENT, 7) == f"Why.\n\n{_block()}\n"
