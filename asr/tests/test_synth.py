import pytest

from config import SYNTH_TEXT_MAX
from synth import SynthTextError, validate_synth_text


def test_validate_synth_text_rejects_empty_text():
    with pytest.raises(SynthTextError, match="empty"):
        validate_synth_text("")


def test_validate_synth_text_rejects_whitespace_only_text():
    with pytest.raises(SynthTextError, match="empty"):
        validate_synth_text("   \n\t  ")


def test_validate_synth_text_rejects_text_over_the_cap_and_names_it():
    with pytest.raises(SynthTextError, match=str(SYNTH_TEXT_MAX)):
        validate_synth_text("a" * (SYNTH_TEXT_MAX + 1))


def test_validate_synth_text_accepts_text_at_the_cap():
    text = "a" * SYNTH_TEXT_MAX
    assert validate_synth_text(text) == text


def test_validate_synth_text_strips_and_returns_the_text():
    assert validate_synth_text("  hello there  ") == "hello there"
