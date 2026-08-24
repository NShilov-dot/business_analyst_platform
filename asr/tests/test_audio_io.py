import io
from pathlib import Path

import httpx
import numpy as np
import pytest
import soundfile as sf

from audio_io import AsrInputError, download, validate_wav, write_temp_wav
from config import EXPECTED_SAMPLE_RATE


def _wav_bytes(sr: int, channels: int, seconds: float = 1.0) -> bytes:
    n = int(sr * seconds)
    data = np.zeros(n, dtype="float32") if channels == 1 else np.zeros((n, channels), dtype="float32")
    buf = io.BytesIO()
    sf.write(buf, data, sr, format="WAV")
    return buf.getvalue()


# --- validate_wav ---------------------------------------------------------


def test_validate_wav_accepts_16k_mono():
    data = _wav_bytes(EXPECTED_SAMPLE_RATE, 1, seconds=2.0)
    audio, duration_ms = validate_wav(data)
    assert audio.ndim == 1
    assert duration_ms == 2000


def test_validate_wav_rejects_wrong_sample_rate():
    data = _wav_bytes(44100, 1)
    with pytest.raises(AsrInputError, match="44100"):
        validate_wav(data)


def test_validate_wav_rejects_stereo():
    data = _wav_bytes(EXPECTED_SAMPLE_RATE, 2)
    with pytest.raises(AsrInputError, match="mono"):
        validate_wav(data)


def test_validate_wav_rejects_garbage_bytes_with_clear_error_not_traceback():
    with pytest.raises(AsrInputError, match="decode"):
        validate_wav(b"this is not a wav file, just random bytes 1234567890")


def test_validate_wav_rejects_empty_audio():
    data = _wav_bytes(EXPECTED_SAMPLE_RATE, 1, seconds=0.0)
    with pytest.raises(AsrInputError, match="empty"):
        validate_wav(data)


def test_write_temp_wav_roundtrips_and_cleans_up():
    audio = np.zeros(16000, dtype="float32")
    with write_temp_wav(audio) as path:
        assert Path(path).exists()
        again, sr = sf.read(path)
        assert sr == EXPECTED_SAMPLE_RATE
        assert len(again) == 16000
    assert not Path(path).exists()


# --- download ---------------------------------------------------------------


def test_download_returns_body_for_a_valid_audio_response():
    body = b"RIFF-fake-wav-body-not-checked-by-download"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "audio/wav"})

    result = download("https://example.com/audio.wav", transport=httpx.MockTransport(handler))
    assert result == body


def test_download_raises_on_http_error_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"forbidden")

    with pytest.raises(AsrInputError, match="403"):
        download("https://example.com/audio.wav", transport=httpx.MockTransport(handler))


def test_download_rejects_xml_error_body_by_content_type():
    xml_body = b"<Error><Code>AccessDenied</Code><Message>Denied</Message></Error>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=xml_body, headers={"content-type": "application/xml"})

    with pytest.raises(AsrInputError, match="content-type"):
        download("https://example.com/audio.wav", transport=httpx.MockTransport(handler))


def test_download_rejects_html_error_body_by_content_type():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not found</html>", headers={"content-type": "text/html"})

    with pytest.raises(AsrInputError, match="content-type"):
        download("https://example.com/audio.wav", transport=httpx.MockTransport(handler))


def test_download_rejects_oversize_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 1000, headers={"content-type": "audio/wav"})

    with pytest.raises(AsrInputError, match="cap"):
        download("https://example.com/audio.wav", transport=httpx.MockTransport(handler), max_bytes=100)


import shutil
import subprocess

import pytest

from audio_io import decode_any_to_16k_mono

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def test_decode_garbage_bytes_gives_clear_error():
    if not _HAS_FFMPEG:
        pytest.skip("ffmpeg not installed locally")
    with pytest.raises(AsrInputError, match="could not decode audio"):
        decode_any_to_16k_mono(b"\x00\x01not audio at all" * 100)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg not installed locally")
def test_decode_webm_opus_roundtrip(tmp_path):
    # Synthesize 1s of 440Hz at 48k stereo, encode to webm/opus (what Chrome's
    # MediaRecorder produces), then decode through the chat-compat path.
    src = tmp_path / "t.webm"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-ac", "2", "-c:a", "libopus", str(src)],
        capture_output=True, check=True,
    )
    audio = decode_any_to_16k_mono(src.read_bytes())
    assert audio.dtype == np.float32
    assert audio.ndim == 1
    assert abs(len(audio) - 16000) < 1600  # ~1s at 16k, opus padding tolerance
