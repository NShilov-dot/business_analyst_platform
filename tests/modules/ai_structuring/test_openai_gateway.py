"""Unit tests for OpenAILlmGateway._parse_analysis (no live OpenAI call)."""

from __future__ import annotations

import json

import pytest

from app.modules.ai_structuring.domain.entities import SUMMARY_CHAR_CAP
from app.modules.ai_structuring.infrastructure.openai_gateway import _parse_analysis


def test_parse_analysis_happy_path() -> None:
    content = json.dumps({"summary": "Кратко: проблема в ручном вводе.", "opening": "Изучил материалы."})
    result = _parse_analysis(content)
    assert result.summary == "Кратко: проблема в ручном вводе."
    assert result.opening == "Изучил материалы."


def test_parse_analysis_strips_and_caps_summary() -> None:
    long_summary = "x" * (SUMMARY_CHAR_CAP + 500)
    content = json.dumps({"summary": f"  {long_summary}  ", "opening": "ok"})
    result = _parse_analysis(content)
    assert len(result.summary) == SUMMARY_CHAR_CAP


def test_parse_analysis_missing_summary_raises() -> None:
    with pytest.raises(ValueError, match="summary"):
        _parse_analysis(json.dumps({"opening": "ok"}))


def test_parse_analysis_missing_opening_raises() -> None:
    with pytest.raises(ValueError, match="opening"):
        _parse_analysis(json.dumps({"summary": "ok"}))


def test_parse_analysis_blank_fields_raise() -> None:
    with pytest.raises(ValueError):
        _parse_analysis(json.dumps({"summary": "   ", "opening": "ok"}))


def test_parse_analysis_non_object_root_raises() -> None:
    with pytest.raises(ValueError, match="not an object"):
        _parse_analysis(json.dumps(["summary", "opening"]))


def test_parse_analysis_rejects_malformed_json() -> None:
    with pytest.raises(json.JSONDecodeError):
        _parse_analysis("not json at all")
