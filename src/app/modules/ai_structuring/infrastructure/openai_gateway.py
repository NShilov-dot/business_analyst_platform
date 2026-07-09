"""OpenAI adapter for LlmPort (Chat Completions, JSON mode).

The provider is deliberately isolated behind LlmPort — swapping to another
vendor (or an on-prem model for the personal-data-localization requirement)
touches only this file plus config.

PII note: chat turns ARE sent to the OpenAI API. This adapter is the
project's first PII-egress integration; enable it only where that is
acceptable, and keep OPENAI_API_KEY unset to disable the feature entirely.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.config import get_settings
from app.modules.ai_structuring.domain.entities import LlmTurn
from app.modules.ai_structuring.domain.errors import LlmUnavailableError

log = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 60.0
_MAX_COMPLETION_TOKENS = 1_200


class OpenAILlmGateway:
    """LlmPort adapter over the OpenAI Chat Completions API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._http = http

    @classmethod
    def from_settings(cls) -> OpenAILlmGateway:
        settings = get_settings()
        api_key = settings.openai_api_key.get_secret_value()
        if not api_key:
            raise LlmUnavailableError(
                "AI intake is not configured (OPENAI_API_KEY is not set)"
            )
        return cls(
            api_key=api_key,
            model=settings.openai_model,
            base_url=str(settings.openai_base_url),
        )

    async def complete_turn(
        self,
        *,
        system_prompt: str,
        history: list[tuple[str, str]],
    ) -> LlmTurn:
        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend({"role": role, "content": content} for role, content in history)

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": _MAX_COMPLETION_TOKENS,
            "temperature": 0.3,
        }

        try:
            if self._http is not None:
                response = await self._post(self._http, payload)
            else:
                async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
                    response = await self._post(client, payload)
        except httpx.HTTPError as exc:
            log.warning("OpenAI request failed: %s", exc)
            raise LlmUnavailableError("LLM provider request failed") from exc

        if response.status_code != 200:
            log.warning(
                "OpenAI returned %s: %s", response.status_code, response.text[:500]
            )
            raise LlmUnavailableError(
                f"LLM provider returned status {response.status_code}"
            )

        try:
            content = response.json()["choices"][0]["message"]["content"]
            return _parse_turn(content)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            log.warning("OpenAI response unparseable: %s", exc)
            raise LlmUnavailableError("LLM provider returned an unparseable response") from exc

    async def _post(
        self, client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> httpx.Response:
        return await client.post(
            f"{self._base_url}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=_TIMEOUT_SECONDS,
        )


def _parse_turn(content: str) -> LlmTurn:
    """Parse the model's JSON contract into an LlmTurn (strict but forgiving)."""
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("LLM JSON root is not an object")

    reply = data.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("LLM JSON has no usable 'reply'")

    raw_draft = data.get("draft")
    draft: dict[str, str] = {}
    if isinstance(raw_draft, dict):
        for key, value in raw_draft.items():
            if isinstance(key, str) and isinstance(value, str):
                draft[key] = value

    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        title = None

    return LlmTurn(
        reply=reply.strip(),
        draft=draft,
        title=title,
        complete=bool(data.get("complete", False)),
    )
