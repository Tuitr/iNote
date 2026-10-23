"""AsrGateway по J1: POST /v1/transcribe (multipart), 15 с, до 2 повторов — вызов идемпотентный."""

import hashlib
from typing import Final

from pydantic import ValidationError

from bot.domain.voice import AudioFile, Transcript, VoiceFailureError, VoiceFailureReason
from common.errors import AppError
from common.http import ServiceHttpClient
from common.logging import getLogger
from contracts.asr import (
    AUDIO_TOO_LONG,
    DEFAULT_LANG,
    FIELD_AUDIO,
    FIELD_LANG,
    FIELD_MIME,
    NO_SPEECH,
    TRANSCRIBE_PATH,
    UNSUPPORTED_FORMAT,
    TranscribeResponse,
)

AUDIO_FILENAME: Final = "voice.ogg"
# Решаем по error.code, а не по статусу (J1); прочее — «сервис недоступен»
_REASONS: Final = {
    NO_SPEECH: VoiceFailureReason.NO_SPEECH,
    AUDIO_TOO_LONG: VoiceFailureReason.TOO_LONG,
    UNSUPPORTED_FORMAT: VoiceFailureReason.UNSUPPORTED,
}

_log = getLogger(__name__)


# Примечание: клиент ASR; токен и X-Trace-Id ставит ServiceHttpClient, повторы — его политика
class HttpAsrGateway:
    # Примечание: http создан в lifespan: база BOT_ASR_URL, 15 с, RetryPolicy(retries=2)
    def __init__(self, http: ServiceHttpClient, lang: str = DEFAULT_LANG) -> None:
        self._http = http
        self._lang = lang

    # Примечание: ключ — хеш аудио: повтор после таймаута шлёт то же тело с тем же ключом
    async def transcribe(self, audio: AudioFile) -> Transcript:
        key = "transcribe:" + hashlib.sha256(audio.content).hexdigest()[:32]
        try:
            response = await self._http.post(
                TRANSCRIBE_PATH,
                files={FIELD_AUDIO: (AUDIO_FILENAME, audio.content, audio.mime)},
                data={FIELD_MIME: audio.mime, FIELD_LANG: self._lang},
                idempotencyKey=key,
            )
        except AppError as exc:
            reason = _REASONS.get(exc.code, VoiceFailureReason.UNAVAILABLE)
            _log.info("asr_failed", error_code=exc.code, reason=reason.value)
            raise VoiceFailureError(reason, exc.code) from exc
        try:
            body = TranscribeResponse.fromJson(response.content)
        except ValidationError as exc:
            raise VoiceFailureError(VoiceFailureReason.UNAVAILABLE, "internal") from exc
        _log.info(
            "asr_transcribed",
            duration_ms=body.durationMs,
            low_confidence=body.lowConfidence,
            text_length=len(body.text),
        )
        return Transcript(
            text=body.text,
            rawText=body.rawText,
            confidence=body.confidence,
            lowConfidence=body.lowConfidence,
            durationMs=body.durationMs,
        )
