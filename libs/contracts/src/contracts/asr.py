"""J1 · bot (C) → asr (A): POST /v1/transcribe, multipart. Владелец — A; здесь сторона потребителя.

ЧЕРНОВИК ПОТРЕБИТЕЛЯ (C): при мерже верх берёт владелец A — его модели заменяют этот файл, бот
подстраивается.

Поля формы: audio (файл), mime (по умолчанию audio/ogg), lang (ru-RU).
Ответ 200 — TranscribeResponse.
Ошибки — стандартный конверт; решаем по error.code, а не по статусу: no_speech 422,
audio_too_long 413, unsupported_format 400, upstream_timeout 504, upstream_unavailable 503.
При мерже верх берёт A.
"""

from typing import Final

from pydantic import Field

from common.wire import WireModel

TRANSCRIBE_PATH: Final = "/v1/transcribe"
FIELD_AUDIO: Final = "audio"
FIELD_MIME: Final = "mime"
FIELD_LANG: Final = "lang"
DEFAULT_MIME: Final = "audio/ogg"
DEFAULT_LANG: Final = "ru-RU"
NO_SPEECH: Final = "no_speech"
AUDIO_TOO_LONG: Final = "audio_too_long"
UNSUPPORTED_FORMAT: Final = "unsupported_format"


# Примечание: распознанная речь: text — нормализованный, raw_text — как услышал провайдер
class TranscribeResponse(WireModel):
    text: str = Field(min_length=1)
    rawText: str
    confidence: float = Field(ge=0, le=1)
    lowConfidence: bool = False
    durationMs: int = Field(ge=0)
