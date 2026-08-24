"""Pure inference glue: a local WAV path -> a TranscriptionResult.

Longform chunking is the model's own job. `transcribe_longform()` segments
via a built-in VAD (pyannote/segmentation-3.0) and already returns segment
and word timestamps in the GLOBAL timescale of the recording
(docs/gigaam-api-findings.md §4) — so there is no chunking.py in this
service and there should not be one. Plan B, only if the gated pyannote
dependency ever becomes unacceptable: swap to silero-vad + `transcribe()`
per detected chunk with a manual global-offset add (~50 lines, see §4).

torch/transformers/pyannote imports are kept INSIDE functions so this module
(and contracts.py/audio_io.py) stay importable in unit tests with no GPU and
none of the heavy ML stack installed.
"""
from __future__ import annotations

from typing import Any

from config import EXPECTED_SAMPLE_RATE, LONGFORM_THRESHOLD_S, MODEL_ID, MODEL_REVISION
from contracts import TranscriptionResult, Utterance, Word, to_ms

# The model's hard reject is defined in SAMPLES (25 * 16000, findings §4
# :1792) — dispatching on a truncated/rounded millisecond duration misroutes
# files within ~1 ms of the boundary into the short path, which then raises.
_LONGFORM_THRESHOLD_SAMPLES = int(LONGFORM_THRESHOLD_S * EXPECTED_SAMPLE_RATE)


def load_model() -> Any:
    """Load the pinned GigaAM revision onto CUDA. Call once per container
    (Modal's @modal.enter()) — never per request."""
    from transformers import AutoModel

    model = AutoModel.from_pretrained(MODEL_ID, revision=MODEL_REVISION, trust_remote_code=True)
    return model.to("cuda")


def warmup_longform(model: Any) -> None:
    """Force GigaAM's internal, lazily-imported pyannote VAD pipeline
    (findings §4) to load now, during @modal.enter(), instead of stalling the
    first real request. Runs transcribe_longform on a ~1s silent clip — the
    only way to trigger the load, since GigaAM manages that pipeline
    internally with no public hook to inject a pre-built one."""
    import numpy as np

    from audio_io import write_temp_wav

    silence = np.zeros(16000, dtype="float32")
    try:
        with write_temp_wav(silence) as path:
            model.transcribe_longform(path, word_timestamps=True)
    except Exception:
        # Best-effort: a warmup surprise must never crash @modal.enter() and
        # boot-loop the container — short-file requests would still work.
        # (The zero-VAD-segments path itself is verified safe: modeling_gigaam
        # returns an empty LongformTranscriptionResult, and the pyannote
        # pipeline — the actual warmup goal — loads before segmentation.)
        import logging

        logging.getLogger(__name__).warning("longform warmup failed", exc_info=True)


def _words_to_contract(words: list[Any] | None) -> list[Word] | None:
    if not words:
        return None
    return [Word(start_ms=to_ms(w.start), end_ms=to_ms(w.end), text=w.text) for w in words]


def _short_utterance(result: Any, duration_ms: int) -> Utterance:
    """<=25s path: model.transcribe() returns one flat result with no segment
    boundaries — derive the utterance span from the first/last word when word
    timestamps are present, else fall back to the full clip span."""
    words = _words_to_contract(getattr(result, "words", None))
    if words:
        start_ms, end_ms = words[0].start_ms, words[-1].end_ms
    else:
        start_ms, end_ms = 0, duration_ms
    return Utterance(start_ms=start_ms, end_ms=end_ms, text=result.text, confidence=None, words=words)


def _segments_to_utterances(segments: list[Any]) -> list[Utterance]:
    """>25s path: one Utterance per model Segment. Segment.start/end and each
    Word.start/end are already in the recording's global timescale (findings
    §1, §4) — only the seconds -> int ms conversion happens here."""
    return [
        Utterance(
            start_ms=to_ms(seg.start),
            end_ms=to_ms(seg.end),
            text=seg.text,
            confidence=None,
            words=_words_to_contract(getattr(seg, "words", None)),
        )
        for seg in segments
    ]


def transcribe(model: Any, wav_path: str, num_samples: int) -> TranscriptionResult:
    """Dispatch on the SAMPLE COUNT (findings §4): model.transcribe() hard-rejects
    anything over 25*16000 samples; transcribe_longform() handles the rest with
    its own built-in VAD chunking. duration_ms is derived from the same sample
    count so the reported duration and the dispatch can never disagree."""
    duration_ms = to_ms(num_samples / EXPECTED_SAMPLE_RATE)
    if num_samples <= _LONGFORM_THRESHOLD_SAMPLES:
        result = model.transcribe(wav_path, word_timestamps=True)
        utterances = [_short_utterance(result, duration_ms)]
    else:
        longform = model.transcribe_longform(wav_path, word_timestamps=True)
        utterances = _segments_to_utterances(longform.segments)

    return TranscriptionResult(
        utterances=utterances,
        detected_language=None,  # no language param/output — findings §2
        model_version=MODEL_REVISION,
        duration_ms=duration_ms,
    )
