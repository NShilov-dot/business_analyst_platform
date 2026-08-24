# modal_app — транскрибация голосовых сообщений на Modal

**Пилотный хостинг.** Сырое голосовое аудио (потенциально ПДн) уходит на
серверы Modal (US cloud) для транскрибации моделью
`ai-sage/GigaChat3.1-Audio-10B-A1.8B`. Это осознанное отступление от NFR о
локализации ПДн в Узбекистане — допустимо **только на время пилота**.
Интеграция спрятана за `TranscriptionPort` в бэкенде: переезд на on-prem
означает удаление этой папки и смену адаптера, без изменений в домене/роутах.

Аудио никогда не сохраняется: браузер → память бэкенда → multipart к Modal →
tempfile внутри контейнера Modal (удаляется по выходу из `with`) → ответ.

## 1. Предпосылки

```bash
pip install modal
modal setup   # открывает браузер, привязывает CLI к вашему Modal-аккаунту/воркспейсу
```

Нужен доступ к воркспейсу Modal с включённым биллингом (GPU L40S не входит в
бесплатный tier).

## 2. Деплой и прогрев весов

```bash
modal deploy modal_app/transcriber.py
```

Деплой печатает базовый URL ASGI-приложения — вида:

```
https://<workspace>--bap-transcriber-transcriber-web.modal.run
```

Это и есть значение для `TRANSCRIPTION_URL` (без `/health`, без `/transcribe`
— только базовый URL, роуты добавляет бэкенд).

**Обязательно** прогреть Volume с весами модели ДО первого реального запроса
— иначе первый `POST /transcribe` скачает ~20 ГБ и упадёт по таймауту:

```bash
modal run modal_app/transcriber.py
```

Повторный прогрев не нужен — веса лежат в персистентном Volume
(`hf-hub-cache`) и переживают редеплои.

## 3. Proxy Auth Token

Modal dashboard → **Settings → Proxy Auth Tokens** → создать новый токен.
Получите пару `Modal-Key` / `Modal-Secret` — это и есть
`TRANSCRIPTION_MODAL_KEY` / `TRANSCRIPTION_MODAL_SECRET`.

**Внимание: токен workspace-scoped**, а не привязан к этому конкретному
приложению. Утечка токена открывает доступ ко **всем** proxy-auth эндпоинтам
воркспейса, не только к транскрибации. Храните только в `.env`/`.env.prod`,
никогда не коммитьте, не логируйте, ротируйте при подозрении на утечку.

## 4. Переменные бэкенда

Три переменные в `.env` бэкенда (см. `backend/.env.example`):

```bash
TRANSCRIPTION_URL=https://<workspace>--bap-transcriber-transcriber-web.modal.run
TRANSCRIPTION_MODAL_KEY=wk-...
TRANSCRIPTION_MODAL_SECRET=ws-...
```

Пустой `TRANSCRIPTION_URL` = фича выключена, роут отвечает 503
(`TRANSCRIPTION_UNAVAILABLE`), остальной чат работает как раньше.

## 5. Смоук-тесты curl

```bash
URL="https://<workspace>--bap-transcriber-transcriber-web.modal.run"

# health, с ключами — {"status":"ok"} (первый вызов после деплоя = cold start,
# может занять 60-90с пока контейнер поднимается и грузит модель)
curl -s "$URL/health" -H "Modal-Key: wk-..." -H "Modal-Secret: ws-..."

# transcribe, с ключами — {"text":"..."}
curl -s -X POST "$URL/transcribe" -F "file=@sample_ru.wav;type=audio/wav" \
     -H "Modal-Key: wk-..." -H "Modal-Secret: ws-..."

# без ключей — 401 (proxy auth блокирует запрос до нашего кода)
curl -si "$URL/health" | head -1
```

## 6. Управление стоимостью

- `max_containers=2` в `@app.cls(...)` — потолок параллельных GPU-контейнеров
  (защита от неограниченного расхода при всплеске нагрузки).
- `scaledown_window=900` — контейнер остаётся тёплым 15 минут после последнего
  запроса, чтобы не платить за холодный старт на каждое сообщение подряд.
- `min_containers=1` (добавить в `@app.cls(...)`, по умолчанию отсутствует) —
  держит один контейнер всегда прогретым, убирает cold start полностью, но
  стоит постоянно: L40S ≈ **2 USD/час** — включайте только на демо/презентацию
  и выключайте (убрать параметр + редеплой) после.
- `timeout=600` — потолок одного вызова (защита от зависшего запроса).

## 7. Обновление промпта/модели

Правки `ASR_PROMPT`, `MAX_NEW_TOKENS`, `MODEL_REVISION` — только в
`transcriber.py`, затем `modal deploy` снова (веса из Volume переиспользуются,
если модель не менялась).
