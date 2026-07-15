# ruff: noqa: RUF001 — the Russian interview prompt legitimately mixes
# Cyrillic prose with Latin JSON keywords; the "confusable" characters are real.
"""System-prompt construction for the intake interview.

The prompt is built from the template version's own FieldDefinition schema, so
new/edited templates change the interview without code changes — the same
property the manual form has.
"""

from __future__ import annotations

import json

from app.modules.intake_templates.domain.entities import FieldDefinition

_SYSTEM_PROMPT_TEMPLATE = """\
Ты — ИИ-ассистент бизнес-аналитика на платформе BuildX (Beeline Uzbekistan).
Твоя задача — в диалоге с бизнес-заказчиком собрать полноценную бизнес-заявку.

Правила интервью:
- Общайся на языке собеседника (по умолчанию — русский), профессионально и кратко.
- Задавай не больше 1–2 вопросов за ход; начинай с сути проблемы.
- Добивайся ИЗМЕРИМОСТИ: метрика успеха должна содержать число, у эффекта
  должна быть базовая линия (AS-IS) и целевое значение (TO-BE).
- Не выдумывай факты за собеседника. Пустые поля оставляй null.
- Когда все обязательные поля заполнены, кратко подведи итог собранной заявки
  и предложи проверить черновик и отправить его в работу кнопкой на экране.

Поля заявки (ключ — описание; required = поле обязательно):
{fields_block}

Формат ответа — СТРОГО один JSON-объект без пояснений вокруг:
{{
  "reply": "<твоя следующая реплика собеседнику>",
  "title": "<краткий заголовок заявки, до 120 символов, или null>",
  "draft": {{{draft_keys}}},
  "complete": <true, если все обязательные поля содержательно заполнены>
}}
В "draft" указывай ВСЕ перечисленные ключи; собранные значения — строками
(консолидируй сказанное, а не цитируй дословно), несобранные — null.
"""


def build_system_prompt(fields: tuple[FieldDefinition, ...]) -> str:
    fields_block = "\n".join(
        f'- {f.key}: {f.label} (required={"true" if f.required else "false"})'
        for f in fields
    )
    draft_keys = ", ".join(f'"{f.key}": <string|null>' for f in fields)
    return _SYSTEM_PROMPT_TEMPLATE.format(fields_block=fields_block, draft_keys=draft_keys)


def coerce_draft(
    raw_draft: dict[str, object], fields: tuple[FieldDefinition, ...]
) -> dict[str, object]:
    """Filter the LLM draft to declared keys with non-empty string values.

    Anything else (unknown keys, nulls, non-strings, blank strings) is dropped
    so the stored draft always passes the same key discipline as the manual
    form and SubmissionValidator sees no unknown-key noise.
    """
    declared = {f.key for f in fields}
    clean: dict[str, object] = {}
    for key, value in raw_draft.items():
        if key not in declared:
            continue
        if isinstance(value, str):
            if value.strip():
                clean[key] = value.strip()
        elif isinstance(value, (int, float, bool)):
            clean[key] = json.dumps(value, ensure_ascii=False)
    return clean
