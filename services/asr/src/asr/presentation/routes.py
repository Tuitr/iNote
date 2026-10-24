"""POST /v1/transcribe (J1): multipart audio + mime + lang → TranscribeResponse или конверт ошибки.

Формат определяем по байтам, поле mime только пишем в лог. Ошибки — кодами J1, тексты для
пользователя пишет бот. Распознанный текст в лог не попадает (правило 10) — только длина и флаги.
В фейковом режиме заголовок `X-Fake-Text` (UTF-8, URL-кодированный) подменяет голосовое текстом.
"""

import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Annotated, Final
from urllib.parse import unquote

from fastapi import APIRouter, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse

from asr.domain.failures import TranscribeError
from asr.domain.transcript import Transcript
from common.errors import AppError, ErrorCode
from common.logging import getLogger
from contracts.asr import (
    DEFAULT_LANG,
    DEFAULT_MIME,
    FIELD_AUDIO,
    FIELD_LANG,
    FIELD_MIME,
    TRANSCRIBE_PATH,
    TranscribeResponse,
)

if TYPE_CHECKING:
    from asr.application.transcribe import TranscribeAudio

FAKE_TEXT_HEADER: Final = "X-Fake-Text"
# язык как у SpeechKit: ru-RU, en-US, kk-KZ
_LANG_RE: Final = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
MIME_MAX_LENGTH: Final = 100

# как из текста X-Fake-Text сделать фейковое голосовое (fake_stt.fakeAudio, через main.py)
type FakeAudioBuilder = Callable[[str], bytes]

_log = getLogger(__name__)


# Примечание: роутер J1; сценарий собирается в lifespan (app.state.transcribe), fakeAudio — только
# в фейковом режиме
def buildTranscribeRouter(*, providerName: str, fakeAudio: FakeAudioBuilder | None) -> APIRouter:
    router = APIRouter()

    # Примечание: одно голосовое → текст; отказ — AppError с кодом и статусом J1
    @router.post(TRANSCRIBE_PATH)
    async def transcribeRoute(
        request: Request,
        audio: Annotated[UploadFile, File(alias=FIELD_AUDIO)],
        mime: Annotated[str, Form(alias=FIELD_MIME, max_length=MIME_MAX_LENGTH)] = DEFAULT_MIME,
        lang: Annotated[str, Form(alias=FIELD_LANG)] = DEFAULT_LANG,
        fakeText: Annotated[str | None, Header(alias=FAKE_TEXT_HEADER)] = None,
    ) -> JSONResponse:
        if not _LANG_RE.fullmatch(lang):
            raise AppError(ErrorCode.VALIDATION_ERROR, "lang — вида ru-RU", {"field": FIELD_LANG})
        content = await audio.read()
        if fakeAudio is not None and fakeText is not None:
            content = fakeAudio(unquote(fakeText))
        transcribe: TranscribeAudio = request.app.state.transcribe
        try:
            transcript = await transcribe(content, lang)
        except TranscribeError as exc:
            _log.info(
                "transcribe_failed",
                error_code=exc.failure.value,
                provider=providerName,
                audio_bytes=len(content),
                mime=mime,
                **exc.details,
            )
            raise AppError(
                exc.failure.value, exc.message, exc.details, httpStatus=exc.httpStatus
            ) from exc
        _log.info(
            "transcribed",
            provider=providerName,
            audio_bytes=len(content),
            duration_ms=transcript.durationMs,
            text_length=len(transcript.text),
            low_confidence=transcript.lowConfidence,
        )
        return JSONResponse(toResponse(transcript).toWire())

    return router


# Примечание: Transcript → тело ответа J1
def toResponse(transcript: Transcript) -> TranscribeResponse:
    return TranscribeResponse(
        text=transcript.text,
        rawText=transcript.rawText,
        confidence=transcript.confidence,
        lowConfidence=transcript.lowConfidence,
        durationMs=transcript.durationMs,
    )
