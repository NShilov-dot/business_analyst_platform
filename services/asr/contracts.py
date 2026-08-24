"""Wire contract for the ASR service's JSON output.

All timestamps are int MILLISECONDS, never float seconds — the model's native
API returns float seconds (docs/gigaam-api-findings.md §1, §8); `to_ms()` is
the single conversion point model.py uses before constructing these.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


def to_ms(seconds: float) -> int:
    """Convert a model float-seconds timestamp to int milliseconds."""
    return round(seconds * 1000)


@dataclass
class Word:
    start_ms: int
    end_ms: int
    text: str


@dataclass
class Utterance:
    start_ms: int
    end_ms: int
    text: str
    confidence: float | None
    words: list[Word] | None


@dataclass
class TranscriptionResult:
    utterances: list[Utterance]
    detected_language: str | None
    model_version: str  # HF revision SHA — persisted to our DB alongside the transcript
    duration_ms: int

    def to_dict(self) -> dict:
        return asdict(self)


class ContractError(ValueError):
    """Raised by validate_monotonic() when a TranscriptionResult violates its invariants."""


def validate_monotonic(result: TranscriptionResult, *, tolerance_ms: int = 100) -> None:
    """Self-check a TranscriptionResult before it goes over the wire.

    Checks, per utterance in order:
    - start_ms <= end_ms
    - start_ms is non-decreasing across utterances (sorted by start)
    - end_ms <= duration_ms + tolerance_ms (VAD segment boundaries can
      overshoot the measured duration slightly — findings §4)

    Raises ContractError naming the offending utterance and values. Used by
    tests and by the endpoint (modal_app.py) as a final check before returning.
    """
    prev_start = -1
    for i, u in enumerate(result.utterances):
        if u.start_ms > u.end_ms:
            raise ContractError(f"utterance {i}: start_ms ({u.start_ms}) > end_ms ({u.end_ms})")
        if u.start_ms < prev_start:
            raise ContractError(
                f"utterance {i}: start_ms ({u.start_ms}) out of order (previous start {prev_start})"
            )
        if u.end_ms > result.duration_ms + tolerance_ms:
            raise ContractError(
                f"utterance {i}: end_ms ({u.end_ms}) exceeds duration_ms "
                f"({result.duration_ms}) + tolerance ({tolerance_ms})"
            )
        prev_start = u.start_ms
