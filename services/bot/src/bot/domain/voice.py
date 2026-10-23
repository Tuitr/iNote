"""Голосовое: скачанный файл, распознанный текст и причины, по которым распознать не вышло."""

from dataclasses import dataclass
from enum import StrEnum


# Примечание: скачанное голосовое — байты и тип, как отдал MAX
@dataclass(frozen=True)
class AudioFile:
    content: bytes
    mime: str


# Примечание: распознанная речь (J1): text — для агента, lowConfidence — решает Brain, не бот
@dataclass(frozen=True)
class Transcript:
    text: str
    rawText: str
    confidence: float
    lowConfidence: bool
    durationMs: int


# Примечание: почему голосовое не стало текстом — от этого зависит, что бот скажет сам
class VoiceFailureReason(StrEnum):
    NO_SPEECH = "no_speech"
    TOO_LONG = "audio_too_long"
    UNSUPPORTED = "unsupported_format"
    UNAVAILABLE = "unavailable"


# Примечание: голосовое не распознано; reason — что сказать, code — код стандарта для логов
class VoiceFailureError(Exception):
    # Примечание: code — исходный error.code ASR или причина скачивания
    def __init__(self, reason: VoiceFailureReason, code: str) -> None:
        super().__init__(f"{reason}: {code}")
        self.reason = reason
        self.code = code
