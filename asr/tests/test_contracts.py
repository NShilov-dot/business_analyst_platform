import pytest

from contracts import ContractError, TranscriptionResult, Utterance, Word, to_ms, validate_monotonic


def test_to_ms_converts_seconds_to_int_milliseconds():
    assert to_ms(1.234) == 1234
    assert to_ms(0.0) == 0


def test_to_ms_rounds_rather_than_truncates():
    assert to_ms(1.2344) == 1234
    assert to_ms(1.2346) == 1235


def test_to_ms_returns_a_plain_int():
    assert isinstance(to_ms(1.5), int)


def _result(utterances: list[Utterance], duration_ms: int) -> TranscriptionResult:
    return TranscriptionResult(
        utterances=utterances, detected_language=None, model_version="deadbeef", duration_ms=duration_ms
    )


def test_validate_monotonic_accepts_a_well_formed_result():
    result = _result(
        [
            Utterance(0, 1000, "hello", None, None),
            Utterance(1000, 2000, "world", None, [Word(1000, 1500, "world")]),
        ],
        duration_ms=2000,
    )
    validate_monotonic(result)  # must not raise


def test_validate_monotonic_allows_small_vad_boundary_overshoot():
    result = _result([Utterance(0, 1090, "close enough", None, None)], duration_ms=1000)
    validate_monotonic(result)  # within the default +100ms tolerance


def test_validate_monotonic_rejects_start_after_end():
    result = _result([Utterance(500, 100, "oops", None, None)], duration_ms=1000)
    with pytest.raises(ContractError, match="start_ms"):
        validate_monotonic(result)


def test_validate_monotonic_rejects_out_of_order_utterances():
    result = _result(
        [Utterance(1000, 1500, "b", None, None), Utterance(0, 500, "a", None, None)], duration_ms=2000
    )
    with pytest.raises(ContractError, match="out of order"):
        validate_monotonic(result)


def test_validate_monotonic_rejects_end_past_duration_beyond_tolerance():
    result = _result([Utterance(0, 5000, "late", None, None)], duration_ms=1000)
    with pytest.raises(ContractError, match="duration_ms"):
        validate_monotonic(result)


def test_to_dict_is_plain_json_serializable_structure():
    result = _result([Utterance(0, 100, "hi", None, [Word(0, 100, "hi")])], duration_ms=100)
    payload = result.to_dict()
    assert payload == {
        "utterances": [
            {"start_ms": 0, "end_ms": 100, "text": "hi", "confidence": None, "words": [{"start_ms": 0, "end_ms": 100, "text": "hi"}]}
        ],
        "detected_language": None,
        "model_version": "deadbeef",
        "duration_ms": 100,
    }
