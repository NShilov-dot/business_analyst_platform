# Интеграция ASR (GigaAM-Multilingual) для бота саммаризации встреч Teams/Zoom

Инструкция для команды/агента проекта «бот встреч»: как переключить транскрибацию
на **ai-sage/GigaAM-Multilingual** — единственную открытую модель, дающую
качественный узбекский (WER 7–13 против 105+ у Whisper large v3), плюс казахский,
киргизский, русский, английский, с **пословными таймкодами**. Сервис уже задеплоен
и принят на реальных файлах; здесь — контракт, клиентский код, эксплуатация и
вариант собственного деплоя. Первоисточники: `docs/gigaam-api-findings.md`
(факты об API модели), `services/asr/` (код сервиса) в репозитории BI_analyst.

---

## 1. Что задеплоено

| | |
|---|---|
| Endpoint | `https://nshilov-dot--bap-asr-asr-web.modal.run` |
| Модель | `ai-sage/GigaAM-Multilingual`, ветка **large_ctc** (600M), ревизия запинена: `3905cd51c3ed4e88c8edf33f3302969ba480a327` |
| GPU | L4 (24 ГБ, ~$0.80/час только пока контейнер жив) |
| Масштабирование | в ноль через 10 мин простоя; `max_containers=2`; потолок вызова 1800 с |
| Замеры | холодный старт **38–40 с**; тёплый: 11 с аудио → 1.4 с, 71 с → 3.2 с (час аудио ≈ 3–5 мин, см. §7) |
| Auth | Modal Proxy Auth: заголовки `Modal-Key` + `Modal-Secret` |

**Токены**: создайте боту СВОЮ пару в дашборде Modal (Settings → Proxy Auth
Tokens), не переиспользуйте чужую — токены workspace-scoped, отдельная пара
позволяет отозвать доступ бота независимо. 401 без токенов не поднимает GPU.

## 2. Контракт

### Запрос (режим встреч — JSON с presigned URL)

```
POST /transcribe
Modal-Key: wk-...
Modal-Secret: ws-...
Content-Type: application/json

{"audio_url": "https://<ваш-S3>/meeting.wav?X-Amz-...(presigned GET)"}
```

Файл НЕ гоняется через тело запроса — часовая встреча ~115 МБ; сервис скачивает
её сам по presigned URL (кап 500 МБ, URL должен быть доступен из интернета).

### Ответ 200

```json
{
  "utterances": [
    {
      "start_ms": 31, "end_ms": 16805,
      "text": "вечерня отошла давно но в кельях тихо и темно",
      "confidence": null,
      "words": [{"start_ms": 31, "end_ms": 480, "text": "вечерня"}, ...]
    }
  ],
  "detected_language": null,
  "model_version": "3905cd51c3ed4e88c8edf33f3302969ba480a327",
  "duration_ms": 71250
}
```

- **Все времена — int миллисекунды в глобальной шкале записи** (склейка окон уже
  сделана сервисом; на приёмке проверена монотонность и попадание в длительность).
- `utterances` = VAD-сегменты по паузам речи (15–22 с); `words` — пословные
  таймкоды (гранулярность ~40 мс, позиции эмиссии CTC — «конец звучания слова»
  может слегка недотягивать; для якорей реплик достаточно).
- `model_version` пишите в свою БД рядом с транскриптом — при смене ревизии
  модели старые транскрипты остаются атрибутированными.
- `confidence` всегда `null` (greedy CTC вероятностей не отдаёт); поле — задел.
- `detected_language` всегда `null`: модель — посимвольный CTC над единым
  алфавитом, языкового параметра/детекции НЕТ. Смешанная ru/uz речь декодируется
  вперемешку: узбекское латиницей, русское кириллицей, в одном потоке. Если
  саммаризатору нужен язык сегмента — определяйте по алфавиту слов на своей
  стороне (латиница+`'` ≈ uz, кириллица ≈ ru/kk/ky).
- **Текст без пунктуации и заглавных букв** (ASR, не LLM). Пунктуацию, если
  нужна, восстанавливайте на этапе саммаризации (LLM это делает попутно).

### Ошибки

| Код | Причина | Что делать |
|---|---|---|
| 401 (без JSON) | нет/неверные Modal-токены | починить секреты |
| 422 | скачивание не удалось (HTTP ≠ 2xx; в detail — статус), тело похоже на ошибку S3 (content-type xml/html, в detail — первые 200 байт), не WAV/не 16 кГц/стерео/пусто, >500 МБ | не ретраить с тем же входом; чинить URL/нормализацию |
| 415 | (multipart-режим) ffmpeg не смог декодировать | битый файл |
| 500 | баг сервиса (внутренний self-check) | лог в Modal: `modal app logs bap-asr` |
| таймаут/5xx Modal | холодный старт/инфра | ретраить безопасно — сервис stateless и идемпотентен |

## 3. Требования к входу — нормализация НА ВАШЕЙ СТОРОНЕ

Сервис строго валидирует: **WAV PCM, 16 кГц, моно** (soundfile, без фолбэков —
чтобы мусор падал с внятной ошибкой, а не в недрах декодера). Нормализация —
CPU-работа, делайте её на своём VPS до аплоада в S3:

```bash
ffmpeg -nostdin -i meeting_raw.m4a -ac 1 -ar 16000 -c:a pcm_s16le meeting.wav
```

(из любого формата, который отдают Teams/Zoom: m4a/mp4/webm/mp3). Час аудио
→ ~115 МБ WAV. Дальше: аплоад в ваш S3 → presigned GET (TTL ≥ 30 мин) → POST.

## 4. Клиентский код (python, httpx)

Критично: **`follow_redirects=True`**. Modal рвёт HTTP-запрос через 150 с и
отдаёт 303-редирект на URL результата; клиент, следующий за редиректами,
прозрачно дожидается конца (часовой файл = несколько хопов). `requests` тоже
умеет (`allow_redirects=True` по умолчанию), но задайте большой read-timeout.

```python
import httpx

ASR_URL = "https://nshilov-dot--bap-asr-asr-web.modal.run"
HEADERS = {"Modal-Key": settings.modal_key, "Modal-Secret": settings.modal_secret}
# read=150s покрывает одно окно Modal; общий бюджет растягивают 303-хопы.
TIMEOUT = httpx.Timeout(150.0, connect=10.0)

def transcribe(presigned_url: str) -> dict:
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
        resp = client.post(f"{ASR_URL}/transcribe", headers=HEADERS,
                           json={"audio_url": presigned_url})
    if resp.status_code == 422:
        raise AsrBadInput(resp.json()["detail"])      # не ретраить
    resp.raise_for_status()                            # 5xx/таймаут — можно ретраить
    return resp.json()
```

### Celery-задача (эскиз конвейера)

```python
@app.task(bind=True, max_retries=3, retry_backoff=60)
def transcribe_meeting(self, meeting_id: str) -> None:
    m = db.get_meeting(meeting_id)                    # WAV уже нормализован и в S3
    url = s3.presigned_get(m.wav_key, expires=3600)
    try:
        result = transcribe(url)
    except AsrBadInput:
        db.mark_failed(meeting_id, reason="bad audio")  # не ретраим
        raise
    except Exception as exc:
        raise self.retry(exc=exc)                     # холодный старт/сеть — ретраим
    db.save_transcript(meeting_id,
                       utterances=result["utterances"],
                       model_version=result["model_version"],
                       duration_ms=result["duration_ms"])
    summarize_meeting.delay(meeting_id)               # следующий шаг — ваш LLM
```

Правила, зашитые в дизайн: сервис **stateless** (URL внутрь, JSON наружу, ничего
не хранит) — всё состояние в вашем Postgres; порядок шагов знает только Celery.
Повторный вызов с тем же URL безопасен.

### Прогрев (опционально)

`GET /health` с токенами поднимает контейнер и грузит модель (~40 с). Дёрните
его fire-and-forget, когда встреча ещё идёт/файл конвертируется — к моменту
POST контейнер будет тёплым. При постоянном потоке встреч можно включить
`min_containers=1` в `services/asr/modal_app.py` (≈$0.80/час непрерывно).

## 5. Разделение ролей моделей (архитектура)

- **GigaAM-Multilingual (этот сервис)** — единственный источник ТЕКСТА и
  таймкодов. Для узбекских встреч альтернатив нет.
- **GigaChat3.1-Audio** (мультимодальная, `bap-transcriber` на L40S) — семантика
  только ru/en: уточнение якорей, «понимание» аудио. НЕ объединяйте их в один
  контейнер: GigaAM требует `transformers==5.*`, GigaChat — `4.57.*`, обе через
  `trust_remote_code`. Это два отдельных Modal-приложения намеренно.
- Саммаризация: подавайте в ваш LLM `utterances` с таймкодами — он получит
  структуру реплик и сможет цитировать моменты времени.

## 6. Свой деплой (если не хотите делить наш)

Вендорите каталог `services/asr/` из BI_analyst (6 модулей + тесты, ~600 строк).

```bash
pip install modal && modal setup
# 1) Секрет: HF read-токен под именем huggingface-secret (ключ HF_TOKEN)
#    + на странице pyannote/segmentation-3.0 в HF нажать "Agree and access"
#    (это gated VAD-модель для нарезки длинных записей)
modal deploy services/asr/modal_app.py     # печатает URL
modal run services/asr/modal_app.py        # префетч весов (~2.3 ГБ) в Volume — ОБЯЗАТЕЛЬНО
# 2) Proxy Auth Token в дашборде → Modal-Key/Modal-Secret клиенту
# 3) Приёмка: services/asr/tests/acceptance.py --endpoint <URL> --url <presigned-wav>
```

**Пины, добытые кровью — не трогайте без причины:**
- `torchcodec==0.10.*` — версии torchcodec спарены 1:1 с минорами torch
  (0.10↔2.10). Непропинованный pip ставит сборку под torch 2.11/CUDA 13 → на
  рантайме «libnvrtc.so.13 not found» / «undefined symbol torch_from_blob».
- `MODEL_REVISION` — SHA, не ветка: `trust_remote_code` исполняет код из HF-репо.
- В `modal_app.py` НЕЛЬЗЯ `from __future__ import annotations`: FastAPI не
  резолвит строковую аннотацию pydantic-модели, объявленной внутри функции, и
  молча превращает JSON-body в query-параметр (все запросы 422).
- pyannote 3.x как «более простая» альтернатива не работает: несовместим с
  torchaudio 2.10 (`AudioMetaData` удалён). Связка авторов: pyannote 4 + torchcodec.

## 7. Эксплуатация и стоимость

- Час встречи ≈ 3–5 мин GPU-времени L4 ≈ **$0.04–0.07 за встречу** + 10 мин
  тёплого хвоста (~$0.13). Хранение весов в Volume — центы/месяц.
- Логи: `modal app logs bap-asr`; контейнеры: `modal container list`;
  аварийный стоп: `modal app stop bap-asr` (вернуть: `modal deploy ...`).
- `timeout=1800` на классе — потолок одного вызова (часовая встреча помещается
  с запасом; для 3-часовых поднимите в `modal_app.py`).
- ⚠️ **ПДн/комплаенс**: аудио встреч уходит в облако Modal (US) и там
  транскрибируется (ничего не сохраняется, tempfile удаляется). Для Беларуси/
  Узбекистана с требованием локализации это допустимо только как пилот — тот же
  вывод, что и для чата BI_analyst. Модель MIT — on-prem перенос кода сервиса
  тривиален (docker с теми же пинами), меняется только URL у клиента.

## 8. Чек-лист переключения (TL;DR)

1. Создать Proxy Auth Token для бота → секреты в конфиг бота.
2. На VPS: ffmpeg-нормализация записи → 16 кГц mono WAV PCM → S3.
3. Celery-задача: presigned GET → `POST /transcribe` (httpx, follow_redirects,
   read-timeout 150 с) → сохранить `utterances`+`model_version` в Postgres.
4. 422 = не ретраить (чинить вход); 5xx/таймаут = ретраить с бэкоффом.
5. Опционально: `GET /health` для прогрева, пока файл конвертируется.
6. Прогнать `tests/acceptance.py` на своём реальном часовом файле (проверит
   монотонность таймкодов и швы окон) — это последний непройденный пункт
   приёмки, ждёт первого настоящего presigned URL.
