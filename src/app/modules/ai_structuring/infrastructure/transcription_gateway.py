"""Modal adapter for TranscriptionPort — a TEMPORARY pilot integration.

The provider is deliberately isolated behind TranscriptionPort — moving the
model on-prem (required to satisfy the personal-data-localization NFR long
term) touches only this file plus config.

PII note: raw voice audio egresses to Modal (US-hosted GPU serverless) for
transcription. This is acceptable only for the pilot; audio is never
persisted anywhere (memory here, a Modal tempfile there, both discarded).

Modal-specific transport quirks:
- Modal web endpoints cut the HTTP response at 150s and reply with a 303
  redirect to a result URL, so the client MUST set follow_redirects=True and
  use a timeout close to (but not over) that window.
- Auth is Modal Proxy Auth: `Modal-Key` / `Modal-Secret` headers (NOT
  `Authorization`) — httpx does not strip these on redirect, unlike
  `Authorization`.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.modules.ai_structuring.domain.errors import TranscriptionUnavailableError

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(150.0, connect=10.0)  # one Modal window; 303-hops extend total time


class ModalTranscriptionGateway:
    """TranscriptionPort adapter over a Modal-hosted GigaChat-Audio ASR app."""

    def __init__(
        self,
        *,
        url: str,
        modal_key: str,
        modal_secret: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._headers = {"Modal-Key": modal_key, "Modal-Secret": modal_secret}
        self._http = http

    @classmethod
    def from_settings(cls) -> ModalTranscriptionGateway:
        settings = get_settings()
        if not settings.transcription_enabled:
            raise TranscriptionUnavailableError(
                "Voice transcription is not configured (TRANSCRIPTION_URL is not set)"
            )
        return cls(
            url=str(settings.transcription_url),
            modal_key=settings.transcription_modal_key.get_secret_value(),
            modal_secret=settings.transcription_modal_secret.get_secret_value(),
        )

    async def _post(
        self, path: str, *, files: dict[str, tuple[str, bytes, str]]
    ) -> httpx.Response:
        """POST via the injected client if present, else a one-off client —
        either way with the same timeout/redirect behavior (Modal's 150s
        response cut answers with a 303 to a result URL)."""
        url = f"{self._url}{path}"
        client = self._http
        if client is not None:
            return await client.post(
                url, files=files, headers=self._headers, timeout=_TIMEOUT, follow_redirects=True
            )
        async with httpx.AsyncClient() as one_off:
            return await one_off.post(
                url, files=files, headers=self._headers, timeout=_TIMEOUT, follow_redirects=True
            )

    async def _get(self, path: str) -> httpx.Response:
        url = f"{self._url}{path}"
        client = self._http
        if client is not None:
            return await client.get(
                url, headers=self._headers, timeout=_TIMEOUT, follow_redirects=True
            )
        async with httpx.AsyncClient() as one_off:
            return await one_off.get(
                url, headers=self._headers, timeout=_TIMEOUT, follow_redirects=True
            )

    async def transcribe(self, *, content: bytes, content_type: str, filename: str) -> str:
        files = {"file": (filename, content, content_type)}
        try:
            response = await self._post("/transcribe", files=files)
        except httpx.HTTPError as exc:
            log.warning("Modal transcription request failed: %s", exc)
            raise TranscriptionUnavailableError("Transcription service request failed") from exc

        if response.status_code != 200:
            log.warning(
                "Modal transcription returned %s: %s",
                response.status_code,
                response.text[:500],
            )
            raise TranscriptionUnavailableError(
                f"Transcription service returned status {response.status_code}"
            )

        try:
            text = response.json().get("text")
        except (ValueError, AttributeError, TypeError, KeyError) as exc:
            # ValueError covers json.JSONDecodeError (non-JSON body); AttributeError
            # covers a valid-but-non-dict JSON root (e.g. a list).
            log.warning("Modal transcription response unparseable: %s", exc)
            raise TranscriptionUnavailableError(
                "Transcription service returned an unparseable response"
            ) from exc
        if not isinstance(text, str):
            log.warning("Modal transcription response missing 'text'")
            raise TranscriptionUnavailableError(
                "Transcription service returned an unparseable response"
            )
        return text.strip()

    async def warmup(self) -> None:
        """Best-effort prewarm — swallows every exception; a slow/failed warmup
        must never surface to the caller (it fires from a fire-and-forget
        background task on chat page open)."""
        try:
            await self._get("/health")
        except Exception as exc:  # best-effort warmup: never propagate
            log.debug("Modal transcription warmup failed: %s", exc)
