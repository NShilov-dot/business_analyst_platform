"""Unit tests for ModalTranscriptionGateway (no live Modal call — httpx.MockTransport)."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai_structuring.domain.errors import TranscriptionUnavailableError
from app.modules.ai_structuring.infrastructure.transcription_gateway import (
    ModalTranscriptionGateway,
)


def _gateway(handler: httpx.MockTransport) -> ModalTranscriptionGateway:
    http = httpx.AsyncClient(transport=handler)
    return ModalTranscriptionGateway(
        url="https://ws--bap-transcriber.modal.run",
        modal_key="wk-key",
        modal_secret="ws-secret",
        http=http,
    )


async def test_transcribe_happy_path_sends_auth_headers_and_multipart() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["content_type"] = request.headers.get("content-type", "")
        captured["body"] = request.content
        return httpx.Response(200, json={"text": " Привет мир "})

    gateway = _gateway(httpx.MockTransport(handler))
    text = await gateway.transcribe(content=b"raw-audio-bytes", content_type="audio/webm", filename="v.webm")

    assert text == "Привет мир"
    headers = captured["headers"]
    assert headers["Modal-Key"] == "wk-key"
    assert headers["Modal-Secret"] == "ws-secret"
    assert "multipart/form-data" in str(captured["content_type"])
    assert b"raw-audio-bytes" in captured["body"]  # type: ignore[operator]


@pytest.mark.parametrize("status_code", [401, 500])
async def test_transcribe_non_200_raises_unavailable(status_code: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="boom")

    gateway = _gateway(httpx.MockTransport(handler))
    with pytest.raises(TranscriptionUnavailableError):
        await gateway.transcribe(content=b"x", content_type="audio/wav", filename="v.wav")


async def test_transcribe_connect_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    gateway = _gateway(httpx.MockTransport(handler))
    with pytest.raises(TranscriptionUnavailableError):
        await gateway.transcribe(content=b"x", content_type="audio/wav", filename="v.wav")


@pytest.mark.parametrize("body", [{}, {"text": 123}, {"text": None}])
async def test_transcribe_missing_or_non_string_text_raises_unavailable(body: dict[str, object]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    gateway = _gateway(httpx.MockTransport(handler))
    with pytest.raises(TranscriptionUnavailableError):
        await gateway.transcribe(content=b"x", content_type="audio/wav", filename="v.wav")


async def test_transcribe_200_with_non_json_body_raises_unavailable() -> None:
    # e.g. an intermediate proxy or the 303-redirect result URL answering with
    # an HTML/empty body instead of JSON.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    gateway = _gateway(httpx.MockTransport(handler))
    with pytest.raises(TranscriptionUnavailableError):
        await gateway.transcribe(content=b"x", content_type="audio/wav", filename="v.wav")


async def test_transcribe_200_with_json_list_body_raises_unavailable() -> None:
    # Valid JSON but a non-dict root -> AttributeError on .get, must not 500.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["unexpected", "list"])

    gateway = _gateway(httpx.MockTransport(handler))
    with pytest.raises(TranscriptionUnavailableError):
        await gateway.transcribe(content=b"x", content_type="audio/wav", filename="v.wav")


async def test_transcribe_follows_redirect_on_injected_client() -> None:
    # Modal's 150s cut answers with a 303 to a result URL; the injected-client
    # path (as tests build it) must follow redirects the same as the
    # internally-built client — both now share the same _post helper.
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/transcribe":
            return httpx.Response(303, headers={"Location": "/result/abc"})
        return httpx.Response(200, json={"text": "done"})

    gateway = _gateway(httpx.MockTransport(handler))
    text = await gateway.transcribe(content=b"x", content_type="audio/wav", filename="v.wav")

    assert text == "done"
    assert calls == ["/transcribe", "/result/abc"]


async def test_synthesize_happy_path_sends_auth_headers_and_json() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.content
        return httpx.Response(200, content=b"RIFF....WAVEfmt ")

    gateway = _gateway(httpx.MockTransport(handler))
    audio = await gateway.synthesize(text="Здравствуйте")

    assert audio == b"RIFF....WAVEfmt "
    headers = captured["headers"]
    assert headers["Modal-Key"] == "wk-key"
    assert headers["Modal-Secret"] == "ws-secret"
    assert "Здравствуйте".encode() in captured["body"]  # type: ignore[operator]


async def test_synthesize_non_200_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    gateway = _gateway(httpx.MockTransport(handler))
    with pytest.raises(TranscriptionUnavailableError):
        await gateway.synthesize(text="hello")


async def test_synthesize_connect_error_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    gateway = _gateway(httpx.MockTransport(handler))
    with pytest.raises(TranscriptionUnavailableError):
        await gateway.synthesize(text="hello")


async def test_synthesize_empty_body_raises_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"")

    gateway = _gateway(httpx.MockTransport(handler))
    with pytest.raises(TranscriptionUnavailableError):
        await gateway.synthesize(text="hello")


async def test_warmup_swallows_every_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    gateway = _gateway(httpx.MockTransport(handler))
    await gateway.warmup()  # must not raise


async def test_warmup_sends_auth_headers_to_health() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        return httpx.Response(200, json={"status": "ok"})

    gateway = _gateway(httpx.MockTransport(handler))
    await gateway.warmup()

    assert str(captured["url"]).endswith("/health")
    headers = captured["headers"]
    assert headers["Modal-Key"] == "wk-key"
    assert headers["Modal-Secret"] == "ws-secret"
