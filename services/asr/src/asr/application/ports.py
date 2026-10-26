"""Порты сценария распознавания: провайдер речи, разбор аудио, кеш по хешу аудио."""

from dataclasses import dataclass
from typing import Protocol

from asr.domain.ogg import AudioInfo
from asr.domain.transcript import Transcript


# Примечание: что уходит провайдеру: байты как есть, язык и то, что узнали из файла
@dataclass(frozen=True)
class AudioClip:
    content: bytes
    lang: str
    info: AudioInfo


# Примечание: ответ провайдера: text — для показа, raw — как ответил провайдер (None — то же, что
# text); confidence=None — провайдер уверенность не сообщает (SpeechKit v1)
@dataclass(frozen=True)
class Recognition:
    text: str
    confidence: float | None
    raw: str | None = None


# Примечание: провайдер речи (SpeechKit, фейк); отказ — TranscribeError с кодом J1
class SpeechToText(Protocol):
    # Примечание: имя провайдера для логов
    @property
    def name(self) -> str: ...

    # Примечание: распознать один фрагмент; повторы и таймауты — внутри адаптера
    async def recognize(self, clip: AudioClip) -> Recognition: ...


# Примечание: длительность и каналы по байтам; не тот формат — TranscribeError(unsupported_format)
class AudioProbe(Protocol):
    # Примечание: разбор без ввода-вывода
    def probe(self, content: bytes) -> AudioInfo: ...


# Примечание: не OGG (голосовые MAX приходят в MP3) → OGG/Opus моно; не аудио — TranscribeError
class AudioConverter(Protocol):
    # Примечание: байты любого аудио → байты OGG/Opus моно
    async def toOggOpus(self, content: bytes) -> bytes: ...


# Примечание: кеш распознаваний; не источник правды — сбой кеша не мешает распознаванию
class TranscriptCache(Protocol):
    # Примечание: сохранённый итог по хешу или None
    async def get(self, digest: str) -> Transcript | None: ...

    # Примечание: сохранить итог по хешу (TTL задаёт адаптер)
    async def put(self, digest: str, transcript: Transcript) -> None: ...
