"""Piper TTS synthesis: text validation + a thin wav-bytes wrapper.

Split out from modal_app.py (same pattern as contracts.py/audio_io.py) so
validate_synth_text() is importable in unit tests with no piper-tts/
onnxruntime installed.
"""
from __future__ import annotations

import io
import wave
from typing import Any

from config import SYNTH_TEXT_MAX


class SynthTextError(ValueError):
    """Raised by validate_synth_text() for empty or over-cap text — the
    message is meant to be surfaced to the caller as-is (422)."""


def validate_synth_text(text: str) -> str:
    """Strip and validate synth text.

    Raises SynthTextError if empty after stripping, or longer than
    SYNTH_TEXT_MAX. Returns the stripped text.
    """
    stripped = text.strip()
    if not stripped:
        raise SynthTextError("text must not be empty")
    if len(stripped) > SYNTH_TEXT_MAX:
        raise SynthTextError(f"text exceeds the {SYNTH_TEXT_MAX} character cap")
    return stripped


def build_wav_bytes(voice: Any, text: str) -> bytes:
    """Synthesize `text` with a loaded PiperVoice and return WAV bytes.

    Current piper-tts API (verified in the deployed container):
    `synthesize(text)` returns an Iterable[AudioChunk] and does NOT touch a
    wave file; the wave-file writer is `synthesize_wav(text, wav_file,
    set_wav_format=True)`, which sets framerate/channels/sampwidth itself.
    Older piper-tts wrote via `synthesize(text, wav_file)` — kept as a
    fallback so a pinned-down downgrade doesn't break this module.
    """
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        if hasattr(voice, "synthesize_wav"):
            voice.synthesize_wav(text, wav_file)
        else:
            voice.synthesize(text, wav_file)
    return buf.getvalue()
