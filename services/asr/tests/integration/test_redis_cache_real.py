"""Кеш распознаваний на настоящем Redis (testcontainers): TTL 7 дней, повтор — из кеша."""

from typing import Final

import pytest
from redis.asyncio import Redis

from asr.application.transcribe import TranscribeAudio, TranscribeLimits, audioDigest
from asr.domain.transcript import Transcript
from asr.infrastructure.fake_stt import FakeAudioProbe, fakeAudio
from asr.infrastructure.redis_cache import CACHE_TTL_S, RedisTranscriptCache, cacheKey
from common.redis import KeyedRedis
from services.asr.tests.fakes import ScriptedStt

pytestmark = pytest.mark.integration

LIMITS: Final = TranscribeLimits(maxBytes=1024 * 1024, maxDurationMs=30_000, lowConfidenceBelow=0.6)


# Примечание: TTL ключа на настоящем Redis — ровно из реестра
async def test_ttlOnRealRedis(redisClient: Redis, uniqueId: str) -> None:
    cache = RedisTranscriptCache(KeyedRedis(redisClient))
    transcript = Transcript("Что сегодня", "что сегодня", 1.0, False, 900)

    await cache.put(uniqueId, transcript)

    ttl = await redisClient.ttl(str(cacheKey(uniqueId)))
    assert CACHE_TTL_S - 5 <= ttl <= CACHE_TTL_S
    assert await cache.get(uniqueId) == transcript


# Примечание: сценарий целиком: второе распознавание того же голосового не идёт к провайдеру
async def test_secondCallHitsCache(redisClient: Redis, uniqueId: str) -> None:
    stt = ScriptedStt()
    transcribe = TranscribeAudio(
        stt, FakeAudioProbe(), RedisTranscriptCache(KeyedRedis(redisClient)), LIMITS
    )
    audio = fakeAudio(f"голосовое {uniqueId}")

    first = await transcribe(audio, "ru-RU")
    second = await transcribe(audio, "ru-RU")

    assert first == second
    assert len(stt.clips) == 1
    assert await redisClient.exists(str(cacheKey(audioDigest(audio, "ru-RU")))) == 1
