"""ВРЕМЕННАЯ ASR-заглушка зоны C (S2) по J1. После мержа заменить на ASR_MODE=fake от A.

POST /v1/transcribe (multipart: audio, mime, lang). Фейковое аудио — «OggS» + текст в UTF-8,
заглушка «распознаёт» этот текст. Спецслучаи — в README. Под SERVICE_TOKEN, как все /v1.
В лог — длина и флаги, без текста (правило 10).
"""

import hmac
import json
import os
import re
import sys
from typing import Annotated, Final

import uvicorn
from fastapi import FastAPI, File, Form, Header, UploadFile
from fastapi.responses import JSONResponse

NAME: Final = "asr"
OWNER: Final = "C (временно, вместо ASR_MODE=fake от A)"
PORT: Final = int(os.environ.get("STUB_PORT", "8000"))
SERVICE_TOKEN: Final = os.environ.get("SERVICE_TOKEN", "")
OGG_MAGIC: Final = b"OggS"
LOW_PREFIX: Final = "~low~"
LONG_PREFIX: Final = "~long~"
DOWN_PREFIX: Final = "~down~"
CONFIDENCE: Final = 0.95
LOW_CONFIDENCE: Final = 0.42
# ~60 мс на символ — правдоподобная длительность фразы
MS_PER_CHAR: Final = 60
_PUNCT_RE: Final = re.compile(r"[^\w\s]", re.UNICODE)

app = FastAPI(title="asr-stub")


# Примечание: одна строка JSON в stdout — как LOG_FORMAT=json
def log(event: str, **fields: object) -> None:
    record = {"service": NAME, "event": event, "owner": OWNER, "stub": True, **fields}
    sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# Примечание: конверт ошибки стандарта с trace_id запроса
def errorResponse(status: int, code: str, message: str, traceId: str | None) -> JSONResponse:
    error: dict[str, object] = {
        "code": code,
        "message": message,
        "details": {},
        "trace_id": traceId,
    }
    headers = {"X-Trace-Id": traceId} if traceId else None
    return JSONResponse({"error": error}, status_code=status, headers=headers)


# Примечание: health для compose; данных нет — ready = live
@app.get("/health/live")
@app.get("/health/ready")
async def health() -> dict[str, object]:
    return {"status": "ok", "checks": {}, "stub": NAME, "owner": OWNER}


# Примечание: J1: формат по байтам (OggS), текст после сигнатуры — «распознанная» речь
@app.post("/v1/transcribe")
async def transcribe(
    audio: Annotated[UploadFile, File()],
    mime: Annotated[str, Form()] = "audio/ogg",
    lang: Annotated[str, Form()] = "ru-RU",
    authorization: Annotated[str | None, Header()] = None,
    xTraceId: Annotated[str | None, Header(alias="X-Trace-Id")] = None,
) -> JSONResponse:
    if (denied := _authError(authorization, xTraceId)) is not None:
        return denied
    content = await audio.read()
    text = content[len(OGG_MAGIC) :].decode("utf-8", errors="replace").strip()
    low = text.startswith(LOW_PREFIX)
    if low:
        text = text.removeprefix(LOW_PREFIX).strip()
    failure = _failure(content, text)
    if failure is not None:
        status, code, message = failure
        log("transcribe_failed", error_code=code, trace_id=xTraceId, mime=mime)
        return errorResponse(status, code, message, xTraceId)
    body = {
        "text": text,
        "raw_text": _PUNCT_RE.sub("", text).lower(),
        "confidence": LOW_CONFIDENCE if low else CONFIDENCE,
        "low_confidence": low,
        "duration_ms": len(text) * MS_PER_CHAR,
    }
    log("transcribed", trace_id=xTraceId, text_length=len(text), low_confidence=low, lang=lang)
    return JSONResponse(body, headers={"X-Trace-Id": xTraceId} if xTraceId else None)


# Примечание: сервисный токен, как в middleware libs/common: нет — 401, чужой — 403
def _authError(authorization: str | None, traceId: str | None) -> JSONResponse | None:
    if not SERVICE_TOKEN:
        return None
    if not authorization or not authorization.startswith("Bearer "):
        return errorResponse(401, "unauthorized", "нужен сервисный токен", traceId)
    if not hmac.compare_digest(authorization.removeprefix("Bearer "), SERVICE_TOKEN):
        return errorResponse(403, "forbidden", "неверный сервисный токен", traceId)
    return None


# Примечание: спецслучаи заглушки (таблица в README): (статус, код, сообщение) или None — успех
def _failure(content: bytes, text: str) -> tuple[int, str, str] | None:
    if not content.startswith(OGG_MAGIC):
        return 400, "unsupported_format", "Нужен OGG/Opus"
    if text.startswith(LONG_PREFIX):
        return 413, "audio_too_long", "Голосовое длиннее 60 с"
    if text.startswith(DOWN_PREFIX):
        return 503, "upstream_unavailable", "Провайдер недоступен"
    if not text:
        return 422, "no_speech", "Речь не распознана"
    return None


# Примечание: точка входа: uvicorn на 0.0.0.0 внутри сети compose
def main() -> None:
    log("stub_started", port=PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_config=None, access_log=False)  # noqa: S104
    log("stub_stopped")


if __name__ == "__main__":
    main()
