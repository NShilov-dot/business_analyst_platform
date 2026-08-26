"""Config constants for the GigaAM ASR service — no logic here, just pins.

Facts behind these choices are verified in docs/gigaam-api-findings.md:
- MODEL_ID / MODEL_REVISION: pinned SHA, `large_ctc` variant — best uz WER of
  the two CTC-head variants, still small (§3), revision pinned per the
  trust_remote_code caveat in the findings header.
- PYANNOTE_MODEL_ID: the gated VAD backbone `transcribe_longform()` loads
  internally (§4) — needs HF_TOKEN once, then serves from the cache Volume.
- LONGFORM_THRESHOLD_S: the model's own transcribe() hard-rejects files longer
  than this (§4); model.py dispatches to transcribe_longform() above it.
"""

MODEL_ID = "ai-sage/GigaAM-Multilingual"
MODEL_REVISION = "3905cd51c3ed4e88c8edf33f3302969ba480a327"  # large_ctc, pinned SHA — never main/branch

PYANNOTE_MODEL_ID = "pyannote/segmentation-3.0"  # gated; used internally by transcribe_longform (findings §4)

GPU = "L4"
VOLUME_NAME = "hf-hub-cache"  # shared with modal_app/transcriber.py's cache — different model, same Volume

# Input validation caps (audio_io.py)
EXPECTED_SAMPLE_RATE = 16000
MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024  # hour-long meeting is ~115 MB; cap well above that
DOWNLOAD_TIMEOUT_S = 300.0  # generous — hour-long file over a possibly slow link

# model.py dispatch boundary (findings §4: transcribe() hard-rejects >25s)
LONGFORM_THRESHOLD_S = 25.0

# Piper TTS voice for /synthesize (docs/voice-turns-design.md §2.1) — Russian
# only, per the phase-1 scope decision (assistant replies are always ru).
# The .json sidecar config lives at the same HF path with ".json" appended.
PIPER_VOICE_REPO = "rhasspy/piper-voices"
PIPER_VOICE_ONNX = "ru/ru_RU/irina/medium/ru_RU-irina-medium.onnx"

SYNTH_TEXT_MAX = 2000
