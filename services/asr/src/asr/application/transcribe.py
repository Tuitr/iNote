"""TranscribeAudio: лимиты → перекодирование не-OGG → разбор файла → кеш → провайдер → кеш.

Лимиты проверяются до провайдера — за слишком длинное голосовое не платим. Пустой ответ провайдера —
`no_speech`. Уверенность: провайдер её не сообщает — 1.0 и `low_confidence=false` (SpeechKit v1);
сообщает — `low_confidence`, если ниже порога (порог знает только ASR, план A). Повтор того же
голосового (бот повторяет вызов с тем же ключом) берётся из кеша и не стоит денег.
"""

import hashlib
from dataclasses import dataclass
from typing import Final

from asr.application.ports import (
    AudioClip,
    AudioConverter,
    AudioProbe,
    SpeechToText,
    TranscriptCache,
)
from asr.domain.failures import Failure, TranscribeError
from asr.domain.ogg import isOgg
from asr.domain.transcript import Transcript, normalizeText
from common.logging import getLogger

FULL_CONFIDENCE: Final = 1.0
# одноканальное аудио: SpeechKit принимает только моно
MAX_CHANNELS: Final = 1

_log = getLogger(__name__)


# Примечание: лимиты сервиса и порог уверенности (из настроек)
@dataclass(frozen=True)
class TranscribeLimits:
    maxBytes: int
    maxDurationMs: int
    lowConfidenceBelow: float


# Примечание: сценарий одного запроса /v1/transcribe
class TranscribeAudio:
    # Примечание: провайдер, разбор файла и кеш — порты; собираются в main.py.
    # converter=None — не-OGG сразу unsupported_format (фейковый провайдер принимает только «OggS»)
    def __init__(
        self,
        stt: SpeechToText,
        probe: AudioProbe,
        cache: TranscriptCache,
        limits: TranscribeLimits,
        converter: AudioConverter | None = None,
    ) -> None:
        self._stt = stt
        self._probe = probe
        self._cache = cache
        self._limits = limits
        self._converter = converter

    # Примечание: голосовое → Transcript или TranscribeError с кодом J1
    async def __call__(self, content: bytes, lang: str) -> Transcript:
        if not content:
            raise TranscribeError(Failure.UNSUPPORTED_FORMAT, "пустой файл")
        if len(content) > self._limits.maxBytes:
            raise TranscribeError(
                Failure.AUDIO_TOO_LONG,
                "голосовое слишком большое",
                {"max_bytes": self._limits.maxBytes},
            )
        if self._converter is not None and not isOgg(content):
            content = await self._converter.toOggOpus(content)
        info = self._probe.probe(content)
        if info.channels > MAX_CHANNELS:
            raise TranscribeError(
                Failure.UNSUPPORTED_FORMAT, "нужно моно", {"channels": info.channels}
            )
        if info.durationMs > self._limits.maxDurationMs:
            raise TranscribeError(
                Failure.AUDIO_TOO_LONG,
                "голосовое слишком длинное",
                {"max_duration_ms": self._limits.maxDurationMs},
            )
        digest = audioDigest(content, lang)
        cached = await self._cache.get(digest)
        if cached is not None:
            _log.info("asr_cache_hit", duration_ms=cached.durationMs)
            return cached
        recognition = await self._stt.recognize(AudioClip(content=content, lang=lang, info=info))
        text = normalizeText(recognition.text)
        if not text:
            raise TranscribeError(Failure.NO_SPEECH, "речь не распознана")
        transcript = Transcript(
            text=text,
            rawText=(recognition.raw if recognition.raw is not None else recognition.text).strip(),
            confidence=_confidence(recognition.confidence),
            lowConfidence=(
                recognition.confidence is not None
                and recognition.confidence < self._limits.lowConfidenceBelow
            ),
            durationMs=info.durationMs,
        )
        await self._cache.put(digest, transcript)
        return transcript


# Примечание: ключ кеша: язык и байты — одно голосовое на разных языках распознаётся по-разному
def audioDigest(content: bytes, lang: str) -> str:
    return hashlib.sha256(lang.encode() + b"\0" + content).hexdigest()


# Примечание: уверенность в [0, 1]; не сообщена провайдером — полная
def _confidence(value: float | None) -> float:
    if value is None:
        return FULL_CONFIDENCE
    return min(max(value, 0.0), FULL_CONFIDENCE)
