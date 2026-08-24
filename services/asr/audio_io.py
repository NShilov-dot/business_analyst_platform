"""Download + validate a presigned-URL WAV before it ever reaches the model.

Our input contract is already-normalized 16 kHz mono WAV PCM (normalization
lives upstream on a VPS, not here — docs/gigaam-api-findings.md §5). We
validate strictly with soundfile and reject anything else outright: no
audioread/librosa fallback, so a bad file fails with a clear message instead
of a confusing decode deep inside the model.
"""
from __future__ import annotations

import io
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import httpx
import numpy as np
import soundfile as sf

from config import DOWNLOAD_TIMEOUT_S, EXPECTED_SAMPLE_RATE, MAX_DOWNLOAD_BYTES


class AsrInputError(ValueError):
    """Any problem with the input audio (download or decode) — the message is
    meant to be surfaced to the caller as-is."""


def download(
    url: str,
    *,
    transport: httpx.BaseTransport | None = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> bytes:
    """Fetch a presigned URL into memory with a hard size cap.

    Raises AsrInputError if: the HTTP status is not 2xx; the content-type
    looks like an error body (xml/html) rather than audio — an S3 error
    document must never reach the decoder as if it were audio bytes; or the
    body exceeds `max_bytes`.

    `transport` lets tests inject an httpx.MockTransport; `max_bytes` lets
    tests exercise the cap without allocating hundreds of MB.
    """
    try:
        with httpx.Client(transport=transport, timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True) as client:
            with client.stream("GET", url) as resp:
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "xml" in content_type.lower() or "html" in content_type.lower():
                    # Bounded read: never buffer a whole (possibly huge) error
                    # body just to quote its head.
                    preview = b""
                    for chunk in resp.iter_bytes():
                        preview += chunk
                        if len(preview) >= 200:
                            break
                    preview = preview[:200]
                    raise AsrInputError(
                        f"download returned content-type={content_type!r} "
                        f"(looks like an error body, not audio): {preview!r}"
                    )
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise AsrInputError(f"download exceeds the {max_bytes} byte cap")
                    chunks.append(chunk)
    except httpx.HTTPStatusError as exc:
        raise AsrInputError(f"download failed: HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise AsrInputError(f"download failed: {exc}") from exc
    return b"".join(chunks)


def validate_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Decode + validate WAV bytes. Returns (audio_float32_mono, duration_ms).

    Raises AsrInputError for: undecodable bytes, wrong sample rate, non-mono
    audio, or empty (zero-sample) audio.
    """
    try:
        audio, sr = sf.read(io.BytesIO(data), dtype="float32")
    except Exception as exc:  # soundfile raises for anything it can't parse as a sound file header
        raise AsrInputError(f"could not decode audio as WAV: {exc}") from exc

    if sr != EXPECTED_SAMPLE_RATE:
        raise AsrInputError(f"expected {EXPECTED_SAMPLE_RATE} Hz sample rate, got {sr} Hz")
    if audio.ndim != 1:
        raise AsrInputError(f"expected mono audio, got {audio.ndim}-dimensional array (shape {audio.shape})")
    if audio.shape[0] == 0:
        raise AsrInputError("audio is empty (zero samples)")

    duration_ms = round(len(audio) / EXPECTED_SAMPLE_RATE * 1000)
    return audio, duration_ms


def decode_any_to_16k_mono(data: bytes) -> np.ndarray:
    """Decode ANY container (webm/opus, ogg, mp4, mp3, wav, ...) to float32
    16 kHz mono via the ffmpeg CLI. This is the chat-compat path: browser
    MediaRecorder blobs are NOT normalized WAV, and normalizing them upstream
    would defeat the point of a voice message. The strict `validate_wav`
    contract stays untouched for the presigned-URL meeting pipeline.

    Raises AsrInputError with the tail of ffmpeg's stderr on undecodable input
    (never a raw ffmpeg traceback)."""
    with tempfile.NamedTemporaryFile(suffix=".bin") as src:
        src.write(data)
        src.flush()
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-i", src.name, "-f", "f32le", "-ac", "1",
             "-ar", str(EXPECTED_SAMPLE_RATE), "pipe:1"],
            capture_output=True,
        )
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace")[-300:]
        raise AsrInputError(f"could not decode audio: {tail}")
    audio: np.ndarray = np.frombuffer(proc.stdout, dtype=np.float32)
    if audio.size == 0:
        raise AsrInputError("audio is empty after decode (zero samples)")
    return audio


@contextmanager
def write_temp_wav(audio: np.ndarray, sample_rate: int = EXPECTED_SAMPLE_RATE) -> Iterator[str]:
    """Yield a path to a temp WAV file holding `audio` — model.transcribe*()
    only accepts file paths, never in-memory arrays (findings §5). Deletes
    the file on exit."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        path = tmp.name
    try:
        sf.write(path, audio, sample_rate, subtype="PCM_16")
        yield path
    finally:
        Path(path).unlink(missing_ok=True)
