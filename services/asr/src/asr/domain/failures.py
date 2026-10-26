"""Почему голосовое не распознано: коды J1, которые видит бот (он решает по коду, не по статусу)."""

from enum import StrEnum
from typing import Final


# Примечание: причины отказа — строки кодов конверта ошибки J1
class Failure(StrEnum):
    NO_SPEECH = "no_speech"
    AUDIO_TOO_LONG = "audio_too_long"
    UNSUPPORTED_FORMAT = "unsupported_format"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_TIMEOUT = "upstream_timeout"


# HTTP-статус у каждой причины (J1, CLAUDE.md)
HTTP_STATUS: Final = {
    Failure.NO_SPEECH: 422,
    Failure.AUDIO_TOO_LONG: 413,
    Failure.UNSUPPORTED_FORMAT: 400,
    Failure.UPSTREAM_UNAVAILABLE: 503,
    Failure.UPSTREAM_TIMEOUT: 504,
}


# Примечание: отказ распознавания; message — для лога и конверта, без текста и аудио пользователя
class TranscribeError(Exception):
    # Примечание: details — только служебные числа и коды (лимиты, статус провайдера)
    def __init__(
        self, failure: Failure, message: str, details: dict[str, object] | None = None
    ) -> None:
        super().__init__(f"{failure}: {message}")
        self.failure = failure
        self.message = message
        self.details: dict[str, object] = dict(details or {})

    # Примечание: HTTP-статус ответа для этой причины
    @property
    def httpStatus(self) -> int:
        return HTTP_STATUS[self.failure]
