"""TranscriptCache в Redis: va:v1:asr:cache:{sha256} на 7 дней (docs/redis-keys.md).

Хранится только итог распознавания, аудио — никогда. Redis — не источник правды (правило 4):
недоступен или в ключе мусор — промах кеша и warning, распознавание идёт дальше.
"""

from typing import Final

from pydantic import ValidationError

from asr.domain.transcript import Transcript
from common.logging import getLogger
from common.redis import KeyedRedis, RedisKey
from contracts.asr import TranscribeResponse

CACHE_TTL_S: Final = 7 * 24 * 60 * 60

_log = getLogger(__name__)


# Примечание: ключ кеша по хешу аудио и языка
def cacheKey(digest: str) -> RedisKey:
    return RedisKey(service="asr", entity="cache", id=digest)


# Примечание: кеш распознаваний; значение — TranscribeResponse в JSON (как ответ J1)
class RedisTranscriptCache:
    # Примечание: store — обёртка с проверкой TTL (common.redis)
    def __init__(self, store: KeyedRedis, ttlS: int = CACHE_TTL_S) -> None:
        self._store = store
        self._ttlS = ttlS

    # Примечание: сохранённый итог или None (нет, битый, Redis недоступен)
    async def get(self, digest: str) -> Transcript | None:
        try:
            raw = await self._store.getValue(cacheKey(digest))
        except Exception:  # noqa: BLE001 — кеш можно потерять
            _log.warning("asr_cache_read_failed")
            return None
        if raw is None:
            return None
        try:
            body = TranscribeResponse.fromJson(raw)
        except ValidationError:
            _log.warning("asr_cache_corrupted")
            return None
        return Transcript(
            text=body.text,
            rawText=body.rawText,
            confidence=body.confidence,
            lowConfidence=body.lowConfidence,
            durationMs=body.durationMs,
        )

    # Примечание: записать итог; сбой — только warning
    async def put(self, digest: str, transcript: Transcript) -> None:
        body = _toResponse(transcript)
        try:
            await self._store.setValue(cacheKey(digest), body.toJson(), ttlS=self._ttlS)
        except Exception:  # noqa: BLE001
            _log.warning("asr_cache_write_failed")


# Примечание: Transcript → модель J1 для хранения
def _toResponse(transcript: Transcript) -> TranscribeResponse:
    return TranscribeResponse(
        text=transcript.text,
        rawText=transcript.rawText,
        confidence=transcript.confidence,
        lowConfidence=transcript.lowConfidence,
        durationMs=transcript.durationMs,
    )
