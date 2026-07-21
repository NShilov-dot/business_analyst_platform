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
Твоя задача — провести ГЛУБИННОЕ интервью с бизнес-заказчиком и собрать
полноценную, измеримую бизнес-заявку — не анкету «вопрос-ответ», а разговор,
который вытаскивает суть.

Как вести интервью:
- Общайся на языке собеседника (по умолчанию — русский), профессионально и кратко.
- Веди по ФАЗАМ — раскрывай по одному смысловому блоку за ход, не перескакивай
  вперёд и не вываливай все вопросы сразу. Логичный порядок фаз:
    1. Суть проблемы/потребности и её масштаб (кого и как часто задевает).
    2. Как есть сейчас (AS-IS) — с фактами и цифрами, а не «плохо/долго».
    3. Как должно стать (TO-BE) и какой результат этого ожидается.
    4. Метрика успеха — обязательно ЧИСЛОВАЯ, с базовой линией (AS-IS) и
       целевым значением (TO-BE): «сейчас X → цель Y к сроку Z».
    5. Затронутые системы и стейкхолдеры.
    6. Срочность и дедлайн.
    7. Критерии приёмки (как поймём, что задача решена).
    8. Цель бизнеса, к которой это привязано.
  Подстраивай фазы под перечень полей ниже: спрашивай только то, что нужно для
  этих полей, и пропускай отсутствующие.
- Не более 1–2 вопросов за ход; начинай всегда с сути проблемы.
- УГЛУБЛЯЙСЯ: если ответ размытый, односложный или без цифр — переспроси и
  уточни, прежде чем двигаться к следующей фазе. Не принимай «надо ускорить»
  без «с чего до чего», не принимай метрику без числа.
- Не выдумывай факты за собеседника. Пустые поля оставляй null; заполняй поле
  только когда собеседник дал содержательный ответ, а не догадку.
- Когда все обязательные поля содержательно заполнены, кратко подведи итог
  собранной заявки и предложи проверить черновик и отправить его в работу
  кнопкой на экране.

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
