# ASR-заглушка (временно, зона C)

> **Временно.** Нужна, чтобы голос S2 шёл по цепочке на стенде до фейка A.
> После мержа заменить на `ASR_MODE=fake` от A (`services/asr`), эту папку удалить. Владелец J1 — A.

`POST /v1/transcribe` по J1 (`libs/contracts/.../asr.py`): multipart `audio`, `mime`, `lang`; под `SERVICE_TOKEN`.
**Фейковое аудио = `OggS` + текст в UTF-8** — заглушка возвращает этот текст как распознанный.
Фейковый MAX собирает такие файлы сам: `POST /dev/max/voice {"max_user_id", "max_chat_id", "text"}`.

| Что прислали | Ответ |
|---|---|
| не начинается с `OggS` | 400 `unsupported_format` |
| `OggS` + пусто | 422 `no_speech` |
| `OggS` + `~low~текст` | 200, `low_confidence: true`, `confidence: 0.42` |
| `OggS` + `~long~…` | 413 `audio_too_long` |
| `OggS` + `~down~…` | 503 `upstream_unavailable` |
| `OggS` + текст | 200, `text` — текст, `raw_text` — без пунктуации в нижнем регистре, `confidence: 0.95` |

В лог — `transcribed` / `transcribe_failed` с `trace_id`, длиной текста и флагами; сам текст не пишем.

Запуск без Docker: `STUB_PORT=8020 uv run python deploy/stub/asr/server.py`.
