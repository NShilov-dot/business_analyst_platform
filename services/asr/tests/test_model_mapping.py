"""Feed duck-typed stand-ins for GigaAM's Word/Segment dataclasses through
model.py's mapping to prove global-ms conversion and utterance derivation —
no torch, no transformers, no model download."""
from dataclasses import dataclass, field

from contracts import TranscriptionResult, Word
from model import transcribe


@dataclass
class FakeWord:
    text: str
    start: float
    end: float


@dataclass
class FakeResult:
    text: str
    words: list[FakeWord] = field(default_factory=list)


@dataclass
class FakeSegment:
    text: str
    start: float
    end: float
    words: list[FakeWord] = field(default_factory=list)


@dataclass
class FakeLongform:
    segments: list[FakeSegment]


class FakeModelShort:
    def transcribe(self, path, word_timestamps=True):
        return FakeResult(
            text="hello world",
            words=[FakeWord("hello", 0.12, 0.5), FakeWord("world", 0.6, 1.02)],
        )

    def transcribe_longform(self, path, word_timestamps=True):
        raise AssertionError("short audio must not call transcribe_longform")


class FakeModelNoWords:
    def transcribe(self, path, word_timestamps=True):
        return FakeResult(text="no words here", words=[])

    def transcribe_longform(self, path, word_timestamps=True):
        raise AssertionError("short audio must not call transcribe_longform")


class FakeModelLong:
    def transcribe(self, path, word_timestamps=True):
        raise AssertionError("long audio must not call transcribe")

    def transcribe_longform(self, path, word_timestamps=True):
        return FakeLongform(
            segments=[
                FakeSegment(
                    "first chunk", 0.0, 10.5, [FakeWord("first", 0.0, 0.4), FakeWord("chunk", 0.5, 10.5)]
                ),
                FakeSegment(
                    "second chunk", 30.2, 45.0, [FakeWord("second", 30.2, 30.9), FakeWord("chunk", 31.0, 45.0)]
                ),
            ]
        )


def test_short_audio_dispatches_to_transcribe_and_derives_span_from_words():
    result = transcribe(FakeModelShort(), "/tmp/fake.wav", num_samples=19200)  # 1.2 s
    assert isinstance(result, TranscriptionResult)
    assert len(result.utterances) == 1
    u = result.utterances[0]
    assert (u.start_ms, u.end_ms) == (120, 1020)  # round(0.12*1000), round(1.02*1000)
    assert u.text == "hello world"
    assert u.confidence is None
    assert u.words == [Word(120, 500, "hello"), Word(600, 1020, "world")]
    assert result.detected_language is None
    assert result.duration_ms == 1200
    assert result.model_version  # pinned SHA constant, non-empty


def test_short_audio_falls_back_to_full_clip_span_without_word_timestamps():
    result = transcribe(FakeModelNoWords(), "/tmp/fake.wav", num_samples=48000)  # 3 s
    u = result.utterances[0]
    assert (u.start_ms, u.end_ms) == (0, 3000)
    assert u.words is None


def test_exact_threshold_sample_count_uses_the_short_path():
    # 25 * 16000 samples is the model's own inclusive limit (findings §4 :1792)
    result = transcribe(FakeModelShort(), "/tmp/fake.wav", num_samples=400_000)
    assert len(result.utterances) == 1
    assert result.duration_ms == 25000


def test_one_sample_over_threshold_dispatches_to_longform():
    # 400_001 samples truncates to 25000 ms — an ms-based dispatch would
    # misroute it into transcribe(), which the model hard-rejects.
    result = transcribe(FakeModelLong(), "/tmp/fake.wav", num_samples=400_001)
    assert len(result.utterances) == 2


def test_long_audio_dispatches_to_transcribe_longform_one_utterance_per_segment():
    result = transcribe(FakeModelLong(), "/tmp/fake.wav", num_samples=960_000)  # 60 s
    assert len(result.utterances) == 2
    first, second = result.utterances
    assert (first.start_ms, first.end_ms) == (0, 10500)
    assert first.words == [Word(0, 400, "first"), Word(500, 10500, "chunk")]
    assert (second.start_ms, second.end_ms) == (30200, 45000)
    assert second.words == [Word(30200, 30900, "second"), Word(31000, 45000, "chunk")]
