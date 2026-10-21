from collections.abc import AsyncIterator

import fakeredis
import pytest
import pytest_asyncio

from common.redis import KeyedRedis, MissingTtlError, RedisKey


# Примечание: KeyedRedis поверх fakeredis — без Docker
@pytest_asyncio.fixture
async def store() -> AsyncIterator[KeyedRedis]:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield KeyedRedis(client)
    await client.aclose()


# Примечание: формат ключа va:v1:<сервис>:<сущность>:<id>
def test_keyFormat() -> None:
    assert str(RedisKey("bot", "upd", "12345")) == "va:v1:bot:upd:12345"


# Примечание: разбор строки обратно в ключ
def test_keyParseRoundTrip() -> None:
    key = RedisKey("agent", "ctx", "u-1")
    assert RedisKey.parse(str(key)) == key


# Примечание: кривые сегменты и чужое пространство имён отклоняются
@pytest.mark.parametrize(
    ("service", "entity", "keyId"),
    [
        ("Bot", "upd", "1"),
        ("bot", "up:d", "1"),
        ("bot", "upd", "a:b"),
        ("", "upd", "1"),
        ("bot", "upd", ""),
        ("bot", "upd", "a b"),
        ("1bot", "upd", "1"),
    ],
)
def test_invalidSegmentsRejected(service: str, entity: str, keyId: str) -> None:
    with pytest.raises(ValueError, match="RedisKey"):
        RedisKey(service, entity, keyId)


# Примечание: parse отклоняет чужие префиксы и неверное число частей
@pytest.mark.parametrize(
    "raw", ["va:v2:bot:upd:1", "x:v1:bot:upd:1", "va:v1:bot:upd", "va:v1:bot:upd:1:2"]
)
def test_parseRejectsForeign(raw: str) -> None:
    with pytest.raises(ValueError, match="не ключ"):
        RedisKey.parse(raw)


# Примечание: запись без TTL — только для белого списка реестра
async def test_ttlRequired(store: KeyedRedis) -> None:
    with pytest.raises(MissingTtlError):
        await store.setValue(RedisKey("bot", "upd", "1"), "1", ttlS=None)
    with pytest.raises(MissingTtlError):
        await store.setValue(RedisKey("bot", "upd", "1"), "1", ttlS=0)
    versionKey = RedisKey("mcp", "ver", "u-1")
    await store.setValue(versionKey, "3", ttlS=None)
    assert await store.ttl(versionKey) == -1


# Примечание: SET NX — первый раз True, повтор False, TTL выставлен
async def test_setIfAbsent(store: KeyedRedis) -> None:
    key = RedisKey("bot", "upd", "42")
    assert await store.setIfAbsent(key, "1", ttlS=86400) is True
    assert await store.setIfAbsent(key, "1", ttlS=86400) is False
    assert 0 < await store.ttl(key) <= 86400


# Примечание: счётчик с TTL окна: TTL ставится при первом INCR и не продлевается
async def test_incrementWithTtl(store: KeyedRedis) -> None:
    key = RedisKey("bot", "rl", "777")
    assert await store.incrementWithTtl(key, ttlS=60) == 1
    await store.client.expire(str(key), 30)
    assert await store.incrementWithTtl(key, ttlS=60) == 2
    assert await store.ttl(key) <= 30


# Примечание: чтение и удаление
async def test_getAndDelete(store: KeyedRedis) -> None:
    key = RedisKey("agent", "ctx", "u-2")
    assert await store.getValue(key) is None
    await store.setValue(key, "{}", ttlS=60)
    assert await store.getValue(key) == "{}"
    assert await store.delete(key) is True
    assert await store.delete(key) is False
