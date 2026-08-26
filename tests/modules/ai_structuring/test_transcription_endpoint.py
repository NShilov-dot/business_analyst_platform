"""Endpoint tests for POST /v1/intake-chat/transcriptions[/speech|/warmup].

Covers:
- happy path (200), content-type normalization (webm;codecs=opus), unsupported
  type (415), oversized upload (413 — proves the middleware exemption works),
  disabled feature with no override (503), empty upload (422), warmup (202).
- speech synthesis: happy path (200, audio/wav bytes), disabled feature (503),
  empty/whitespace text (422), over-max-length text (422, pydantic).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import httpx

from app.core import deps
from app.core.security import Principal
from app.modules.ai_structuring.domain.entities import AUDIO_MAX_BYTES
from app.modules.ai_structuring.interface import router as intake_router_module

_TID = UUID("11111111-1111-1111-1111-111111111111")


def _principal(*roles: str) -> Principal:
    return Principal(
        subject="b79ac28a-7f95-4b15-8e22-52308f55eb99",
        tenant_id=_TID,
        roles=frozenset(roles) or frozenset({"tenant_user"}),
        raw_claims={},
    )


class _FakeTranscriber:
    def __init__(self, *, text: str = "hello world", audio: bytes = b"RIFF....WAVEfmt ") -> None:
        self._text = text
        self._audio = audio
        self.received: bytes | None = None
        self.received_content_type: str | None = None
        self.synthesized_text: str | None = None
        self.warmup_called = False

    async def transcribe(self, *, content: bytes, content_type: str, filename: str) -> str:
        self.received = content
        self.received_content_type = content_type
        return self._text

    async def synthesize(self, *, text: str) -> bytes:
        self.synthesized_text = text
        return self._audio

    async def warmup(self) -> None:
        self.warmup_called = True


async def _make_client(
    *, override_transcriber: bool = True, fake: _FakeTranscriber | None = None
) -> AsyncIterator[tuple[httpx.AsyncClient, _FakeTranscriber | None]]:
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[deps._principal] = lambda: _principal("tenant_user")
    app.dependency_overrides[deps.check_rate_limit] = lambda: None
    app.dependency_overrides[deps.check_csrf] = lambda: None
    if override_transcriber:
        fake = fake or _FakeTranscriber()
        app.dependency_overrides[intake_router_module._transcriber] = lambda: fake
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as ac:
        yield ac, fake


async def test_transcribe_happy_path() -> None:
    fake = _FakeTranscriber(text="Привет мир")
    async for ac, f in _make_client(fake=fake):
        r = await ac.post(
            "/v1/intake-chat/transcriptions",
            files={"file": ("v.webm", b"raw-bytes", "audio/webm")},
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["text"] == "Привет мир"
        assert f is not None
        assert f.received == b"raw-bytes"


async def test_transcribe_normalizes_codecs_suffix() -> None:
    fake = _FakeTranscriber()
    async for ac, f in _make_client(fake=fake):
        r = await ac.post(
            "/v1/intake-chat/transcriptions",
            files={"file": ("v.webm", b"raw-bytes", "audio/webm;codecs=opus")},
        )
        assert r.status_code == 200, r.text
        assert f is not None
        assert f.received_content_type == "audio/webm"


async def test_transcribe_unsupported_content_type_rejected() -> None:
    async for ac, _ in _make_client():
        r = await ac.post(
            "/v1/intake-chat/transcriptions",
            files={"file": ("v.txt", b"hello", "text/plain")},
        )
        assert r.status_code == 415
        assert r.json()["error"]["code"] == "UNSUPPORTED_AUDIO_TYPE"


async def test_transcribe_oversized_upload_rejected() -> None:
    # Proves the /v1/intake-chat/transcriptions prefix is exempt from the
    # global 1 MiB LimitBodySizeMiddleware cap — otherwise this would be a
    # generic PAYLOAD_TOO_LARGE well below AUDIO_MAX_BYTES.
    oversized = b"x" * (AUDIO_MAX_BYTES + 1)
    async for ac, _ in _make_client():
        r = await ac.post(
            "/v1/intake-chat/transcriptions",
            files={"file": ("v.wav", oversized, "audio/wav")},
        )
        assert r.status_code == 413
        assert r.json()["error"]["code"] == "AUDIO_TOO_LARGE"


async def test_transcribe_disabled_feature_returns_503() -> None:
    # No override for _transcriber: falls through to the real dependency,
    # which reads settings — conftest leaves TRANSCRIPTION_* unset.
    async for ac, _ in _make_client(override_transcriber=False):
        r = await ac.post(
            "/v1/intake-chat/transcriptions",
            files={"file": ("v.wav", b"some-bytes", "audio/wav")},
        )
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "TRANSCRIPTION_UNAVAILABLE"


async def test_transcribe_oversized_content_length_header_rejected() -> None:
    # An honest Content-Length declaring an oversized body must be rejected
    # even when the actual body sent is tiny (a lying/chunked client bypasses
    # this and is caught by _read_audio_capped instead — see router.py).
    oversized_declared = AUDIO_MAX_BYTES + (128 * 1024)
    async for ac, _ in _make_client():
        request = ac.build_request(
            "POST",
            "/v1/intake-chat/transcriptions",
            files={"file": ("v.wav", b"tiny-body", "audio/wav")},
        )
        request.headers["content-length"] = str(oversized_declared)
        r = await ac.send(request)
        assert r.status_code == 413
        assert r.json()["error"]["code"] == "AUDIO_TOO_LARGE"


async def test_transcribe_empty_upload_rejected() -> None:
    async for ac, _ in _make_client():
        r = await ac.post(
            "/v1/intake-chat/transcriptions",
            files={"file": ("v.wav", b"", "audio/wav")},
        )
        assert r.status_code == 422


async def test_synthesize_speech_happy_path() -> None:
    fake = _FakeTranscriber(audio=b"RIFF....WAVEfmt ")
    async for ac, f in _make_client(fake=fake):
        r = await ac.post(
            "/v1/intake-chat/transcriptions/speech",
            json={"text": "Здравствуйте, чем могу помочь?"},
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "audio/wav"
        assert r.content == b"RIFF....WAVEfmt "
        assert f is not None
        assert f.synthesized_text == "Здравствуйте, чем могу помочь?"


async def test_synthesize_speech_disabled_feature_returns_503() -> None:
    # No override for _transcriber: falls through to the real dependency,
    # which reads settings — conftest leaves TRANSCRIPTION_* unset.
    async for ac, _ in _make_client(override_transcriber=False):
        r = await ac.post(
            "/v1/intake-chat/transcriptions/speech",
            json={"text": "hello"},
        )
        assert r.status_code == 503
        assert r.json()["error"]["code"] == "TRANSCRIPTION_UNAVAILABLE"


async def test_synthesize_speech_empty_text_rejected() -> None:
    async for ac, _ in _make_client():
        r = await ac.post(
            "/v1/intake-chat/transcriptions/speech",
            json={"text": ""},
        )
        assert r.status_code == 422


async def test_synthesize_speech_whitespace_only_text_rejected() -> None:
    async for ac, _ in _make_client():
        r = await ac.post(
            "/v1/intake-chat/transcriptions/speech",
            json={"text": "   "},
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "CHAT_VALIDATION_ERROR"


async def test_synthesize_speech_text_over_max_length_rejected() -> None:
    async for ac, _ in _make_client():
        r = await ac.post(
            "/v1/intake-chat/transcriptions/speech",
            json={"text": "x" * 2001},
        )
        assert r.status_code == 422


async def test_warmup_returns_202_and_calls_warmup() -> None:
    fake = _FakeTranscriber()
    async for ac, f in _make_client(fake=fake):
        r = await ac.post("/v1/intake-chat/transcriptions/warmup")
        assert r.status_code == 202, r.text
        assert r.json()["data"]["status"] == "warming"
        assert f is not None
        assert f.warmup_called is True


async def test_warmup_returns_202_even_when_disabled() -> None:
    async for ac, _ in _make_client(override_transcriber=False):
        r = await ac.post("/v1/intake-chat/transcriptions/warmup")
        assert r.status_code == 202, r.text
