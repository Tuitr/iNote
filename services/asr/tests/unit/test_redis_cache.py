"""RedisTranscriptCache на fakeredis: ключ и TTL по реестру, битое значение и сбой Redis — промах.

Ключ и TTL сверяются со строкой docs/redis-keys.md — реестр и код не расходятся.
"""

from pathlib import Path
from typing import Final

import fakeredis
import pytest

from asr.domain.transcript import Transcript
from asr.infrastructure.redis_cache import CACHE_TTL_S, RedisTranscriptCache, cacheKey
from common.redis import KeyedRedis
from common.testing.redis_registry import loadRegistry, rowFor

REGISTRY: Final = Path(__file__).resolve().parents[4] / "docs" / "redis-keys.md"
DIGEST: Final = "a" * 64
TRANSCRIPT: Final = Transcript("Что у меня завтра", "что у меня завтра", 1.0, False, 1500)


# Примечание: кеш на свежем fakeredis
@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


# Примечание: записали — прочитали то же; TTL — 7 дней
async def test_roundTrip(redis: fakeredis.FakeAsyncRedis) -> None:
    cache = RedisTranscriptCache(KeyedRedis(redis))

    await cache.put(DIGEST, TRANSCRIPT)

    assert await cache.get(DIGEST) == TRANSCRIPT
    assert await redis.ttl(str(cacheKey(DIGEST))) == CACHE_TTL_S


# Примечание: нет ключа — None
async def test_missIsNone(redis: fakeredis.FakeAsyncRedis) -> None:
    assert await RedisTranscriptCache(KeyedRedis(redis)).get(DIGEST) is None


# Примечание: мусор в ключе — промах, а не ошибка запроса
async def test_corruptedValueIsMiss(redis: fakeredis.FakeAsyncRedis) -> None:
    await redis.set(str(cacheKey(DIGEST)), "{не json", ex=60)

    assert await RedisTranscriptCache(KeyedRedis(redis)).get(DIGEST) is None


# Примечание: Redis недоступен — чтение даёт промах, запись молча пропускается
async def test_redisDownIsIgnored() -> None:
    server = fakeredis.FakeServer()
    server.connected = False
    cache = RedisTranscriptCache(KeyedRedis(fakeredis.FakeAsyncRedis(server=server)))

    await cache.put(DIGEST, TRANSCRIPT)

    assert await cache.get(DIGEST) is None


# Примечание: ключ и TTL — как в реестре Redis (J7)
def test_keyAndTtlMatchRegistry() -> None:
    key = str(cacheKey(DIGEST))
    row = rowFor(key, loadRegistry(REGISTRY))

    assert key == f"va:v1:asr:cache:{DIGEST}"
    assert row is not None
    assert row.ttlS == CACHE_TTL_S
