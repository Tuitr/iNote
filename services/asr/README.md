# services/asr — распознавание речи (J1)

Превращает голосовое пользователя в текст для агента. Бот присылает байты голосового из MAX, ASR
проверяет формат и лимиты, отправляет аудио провайдеру и возвращает текст, готовый к показу. Провайдер
скрыт за портом `SpeechToText` и выбирается переменной `ASR_PROVIDER`: бот о нём не знает.

Владелец стыка J1 и зоны по плану — **A**. Сервис написан в S5 по контракту J1 и плану A
(`docs/A · Речь (ASR).md`), чтобы подключить настоящий SpeechKit; при появлении кода A верх у A.

| Провайдер | `ASR_PROVIDER` | Когда |
|---|---|---|
| Фейк: «OggS» + текст → текст, спецслучаи по префиксу | `fake` | стенд `mock`, e2e, CI — без ключей и денег |
| Yandex SpeechKit v1, синхронное распознавание | `yandex` | стенд `full` (`make up PROFILE=full`) — **платно** |

## Слои

```
domain/          ogg.py — формат по байтам (OggS), длительность и каналы OGG/Opus без ffmpeg;
                 transcript.py — Transcript и нормализация; failures.py — коды J1 и их статусы
application/     порты SpeechToText, AudioProbe, AudioConverter, TranscriptCache; TranscribeAudio
infrastructure/  YandexSpeechKit, FakeSpeechToText + FakeAudioProbe, OggOpusProbe,
                 FfmpegOggConverter (не OGG → OGG/Opus), RedisTranscriptCache, AsrSettings
presentation/    POST /v1/transcribe (multipart audio, mime, lang)
main.py          composition root: createApp() и lifespan
```

## Эндпоинты

| Путь | Кто зовёт | Что делает |
|---|---|---|
| `POST /v1/transcribe` | бот, сервисный токен | J1: multipart `audio`, `mime`, `lang` → `text`, `raw_text`, `confidence`, `low_confidence`, `duration_ms` |
| `GET /health/live`, `/health/ready` | compose | live — 200; ready — Redis (провайдера не дёргаем — запрос платный) |

Ошибки — общий конверт, бот решает по `error.code`:

| Код | Статус | Когда |
|---|---|---|
| `no_speech` | 422 | провайдер ничего не распознал (пустой ответ) |
| `audio_too_long` | 413 | больше `ASR_MAX_BYTES` или дольше `ASR_MAX_DURATION_S` — до запроса к провайдеру |
| `unsupported_format` | 400 | не OGG/Opus, битый файл, стерео; SpeechKit не принял аудио (4xx) |
| `upstream_unavailable` | 503 | 5xx и сеть после повтора; ключ отклонён (401/403); квота (429) |
| `upstream_timeout` | 504 | провайдер не уложился в `ASR_DEADLINE_S` |

## Как работает

1. **Формат и лимиты — до провайдера.** Формат — по сигнатуре `OggS` и заголовку `OpusHead`, а не по
   `mime`. Длительность — по грануле последней страницы OGG минус pre-skip (RFC 7845), без ffmpeg.
   SpeechKit синхронно берёт до 1 МБ, 30 с и один канал: всё, что больше, отклоняется сразу и бесплатно.
2. **Кеш.** `va:v1:asr:cache:{sha256(lang + аудио)}` на 7 дней — повтор того же голосового (бот
   повторяет вызов с тем же ключом идемпотентности) не идёт к провайдеру. Хранится только ответ J1,
   аудио — нигде. Redis недоступен — работаем без кеша, `ready` = 503.
3. **SpeechKit.** `POST stt:recognize?lang=ru-RU&format=oggopus&topic=general&rawResults=false`, тело —
   байты как есть, `Authorization: Api-Key …`. Числа цифрами делает SpeechKit (`rawResults=false`).
   Одна попытка — `ASR_PROVIDER_TIMEOUT_S` (6 с), повтор — только на 5xx, таймаут и сеть; всё вместе —
   не дольше `ASR_DEADLINE_S` (13 с), чтобы ответ успел к боту (он ждёт 15 с на попытку).
4. **Текст.** `text` — без лишних пробелов, у SpeechKit — с заглавной буквы (он отдаёт строчными);
   фейк — как есть; `raw_text` — как ответил провайдер.
   SpeechKit v1 уверенность не сообщает: `confidence = 1.0`, `low_confidence = false`. Для фейка и
   будущих провайдеров с уверенностью порог — `ASR_LOW_CONFIDENCE_BELOW`.
5. **Логи.** Распознанный текст, аудио и ключ в лог не пишутся — только длины, длительность, флаги,
   статус и `error_code` провайдера (правило 10).

## Переменные

Таблица генерируется из `AsrSettings` (`make env-docs`), тест следит, чтобы она не расходилась с кодом.
Ключ — только в `.env` (он в `.gitignore`).

<!-- env:begin -->
| Переменная | По умолчанию | Зачем |
|---|---|---|
| `ASR_PROVIDER` | `fake` | `fake` — «OggS» + текст, без ключей и денег; `yandex` — SpeechKit (платно) |
| `ASR_YANDEX_API_KEY` | — | API-ключ сервисного аккаунта (роль `ai.speechkit-stt.user`), для `yandex` |
| `ASR_YANDEX_FOLDER_ID` | — | id каталога — только для ключа пользователя; для сервисного аккаунта пусто |
| `ASR_YANDEX_URL` | `https://stt.api.cloud.yandex.net/speech/v1/stt:recognize` | адрес синхронного распознавания SpeechKit v1 |
| `ASR_PROVIDER_TIMEOUT_S` | `6.0` | таймаут одной попытки к провайдеру, с |
| `ASR_PROVIDER_RETRIES` | `1` | повторы к провайдеру на 5xx и сетевые ошибки (в пределах ASR_DEADLINE_S) |
| `ASR_DEADLINE_S` | `13.0` | весь запрос к провайдеру с повторами, с; меньше таймаута бота (15 с) |
| `ASR_MAX_BYTES` | `1048576` | лимит голосового, байт; больше — `audio_too_long` (SpeechKit — до 1 МБ) |
| `ASR_MAX_DURATION_S` | `30` | лимит длительности, с; дольше — `audio_too_long` (SpeechKit — до 30 с) |
| `ASR_LOW_CONFIDENCE_BELOW` | `0.6` | уверенность ниже — `low_confidence` (если провайдер её сообщает) |
| `DATABASE_URL` | — | общая: PostgreSQL (зона C не читает — для D) |
| `REDIS_URL` | `redis://localhost:6379/0` | общая: Redis (ключи — `docs/redis-keys.md`) |
| `SERVICE_TOKEN` | обязательно | общая: сервисный токен внутренних вызовов (≥ 16 символов; на стенде с HTTPS ≥ 32) |
| `LOG_LEVEL` | `INFO` | общая: уровень логов: DEBUG, INFO, WARNING, ERROR |
| `LOG_FORMAT` | `console` | общая: `console` или `json` |
| `DEFAULT_TZ` | `Europe/Moscow` | общая: пояс по умолчанию (IANA) |
<!-- env:end -->

## Ключ SpeechKit

1. Yandex Cloud → каталог → **Сервисные аккаунты** → создать аккаунт с ролью `ai.speechkit-stt.user`.
2. В аккаунте → **Создать новый ключ** → **API-ключ** (область — SpeechKit).
3. В `.env`: `ASR_YANDEX_API_KEY=<ключ>`, `ASR_YANDEX_FOLDER_ID=` — пусто (для ключа сервисного
   аккаунта каталог не передаётся).

## Запустить и попробовать

```bash
make up                                   # mock: asr с фейковым провайдером
make up PROFILE=full                      # full: asr с SpeechKit (ключ из .env) — платно
make asr-try FILE=voice.ogg PROFILE=full  # распознать свой файл сервисом стенда — платно
```

Голосовое для проверки на Mac: `ffmpeg -f avfoundation -i ":0" -t 5 -ac 1 -c:a libopus voice.ogg`
или из «Диктофона»: `ffmpeg -i memo.m4a -ac 1 -c:a libopus -b:a 24k voice.ogg`.

Руками, без compose:

```bash
docker run -d --rm -p 6379:6379 redis:7.4-alpine
SERVICE_TOKEN=$(openssl rand -hex 16) ASR_PROVIDER=fake \
  uv run uvicorn asr.main:createApp --factory --port 8003
curl -s localhost:8003/v1/transcribe -H "Authorization: Bearer $SERVICE_TOKEN" \
  -F 'audio=@-;filename=v.ogg' -F lang=ru-RU <<< 'OggSчто у меня завтра'
```

Фейк: после `OggS` — текст; пусто — `no_speech`, `~low~ текст` — неуверенно, `~long~` — слишком
длинное, `~down~` — провайдер недоступен. Заголовок `X-Fake-Text` (URL-кодированный UTF-8) подменяет
голосовое текстом — только в режиме `fake`.

## Тесты

```bash
uv run pytest services/asr                       # unit: OGG, сценарий, SpeechKit на MockTransport, приложение
uv run pytest tests/deploy/test_j1_bot_asr.py    # J1 целиком: клиент бота ↔ сервис ASR
uv run pytest services/asr -m "integration or lifecycle"   # Redis в Docker; uvicorn + SIGTERM
```

Настоящих запросов к SpeechKit тесты не делают. Фикстуры `tests/fixtures/*.ogg` — синус от ffmpeg
(libopus моно и стерео, Vorbis), команды — в `tests/oggdata.py`.

## Ограничения

- **SpeechKit получает только OGG/Opus, моно, до 30 с и 1 МБ** — так принимает синхронный SpeechKit.
  Голосовые MAX приходят в MP3 (`audio/mpeg`): при `ASR_PROVIDER=yandex` всё, что не OGG,
  перекодируется ffmpeg в OGG/Opus моно (`FfmpegOggConverter`, ffmpeg ставится в образ asr);
  длиннее 30 с — `audio_too_long`. Нарезка или асинхронное распознавание — план A на S3.
- **Уверенности нет** у SpeechKit v1 — «правильно понял?» по неуверенному распознаванию не сработает.
  Даёт её SpeechKit v3 (gRPC/REST) — следующий шаг, если понадобится.
