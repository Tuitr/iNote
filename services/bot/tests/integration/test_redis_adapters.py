import pytest
from redis.asyncio import Redis

from bot.application.ports import StoredAction
from bot.infrastructure.redis_adapters import (
    CALLBACK_TTL_S,
    DEDUP_TTL_S,
    MARKER_TTL_S,
    NOTIFICATION_TTL_S,
    RATE_WINDOW_S,
    RedisCallbackStore,
    RedisMarkerStore,
    RedisNotificationLedger,
    RedisRateLimiter,
    RedisUpdateDeduplicator,
)
from common.redis import KeyedRedis

pytestmark = pytest.mark.integration


# Примечание: дедуп на настоящем Redis: ключ va:v1:bot:upd:{id}, TTL 24 ч
async def test_dedupKeyAndTtl(redisClient: Redis, uniqueId: str) -> None:
    dedup = RedisUpdateDeduplicator(KeyedRedis(redisClient))
    updateId = f"msg-{uniqueId}"
    assert await dedup.isFirstSeen(updateId) is True
    assert await dedup.isFirstSeen(updateId) is False
    ttl = await redisClient.ttl(f"va:v1:bot:upd:{updateId}")
    assert DEDUP_TTL_S - 5 < ttl <= DEDUP_TTL_S


# Примечание: лимит 20 в минуту: 21-е отклонено с предупреждением, 22-е — молча; окно 60 с
async def test_rateLimitWindow(redisClient: Redis, uniqueId: str) -> None:
    limiter = RedisRateLimiter(KeyedRedis(redisClient), limit=20)
    userId = int(uniqueId[:12], 16)
    decisions = [await limiter.check(userId) for _ in range(22)]
    assert [d.allowed for d in decisions] == [True] * 20 + [False, False]
    assert [d.notify for d in decisions[20:]] == [True, False]
    ttl = await redisClient.ttl(f"va:v1:bot:rl:{userId}")
    assert 0 < ttl <= RATE_WINDOW_S
    assert (await limiter.check(userId + 1)).allowed is True


# Примечание: marker в Redis: пусто → None, после save — значение и TTL 24 ч
async def test_markerStore(redisClient: Redis) -> None:
    await redisClient.delete("va:v1:bot:marker:poll")
    store = RedisMarkerStore(KeyedRedis(redisClient))
    assert await store.load() is None
    await store.save(12345)
    assert await store.load() == 12345
    ttl = await redisClient.ttl("va:v1:bot:marker:poll")
    assert MARKER_TTL_S - 5 < ttl <= MARKER_TTL_S


# Примечание: кнопки на настоящем Redis: ключ va:v1:bot:cb:{key}, TTL 24 ч, чужой payload мимо
async def test_callbackKeyAndTtl(redisClient: Redis, uniqueId: str) -> None:
    store = RedisCallbackStore(KeyedRedis(redisClient))
    key = await store.save(StoredAction("undo", {"item_id": uniqueId, "n": 1, "ok": True}))
    assert len(key) == 16
    ttl = await redisClient.ttl(f"va:v1:bot:cb:{key}")
    assert CALLBACK_TTL_S - 5 < ttl <= CALLBACK_TTL_S
    loaded = await store.load(key)
    assert loaded == StoredAction("undo", {"item_id": uniqueId, "n": 1, "ok": True})
    assert await store.load("undo:7f3a9c2e-5b1d") is None
    assert await store.load("A" * 16) is None
    await redisClient.set(f"va:v1:bot:cb:{'B' * 16}", "{битый", ex=60)
    assert await store.load("B" * 16) is None
    stored = StoredAction("undo", {"item_id": uniqueId}, chatId=500600700, label="Отменить")
    bound = await store.save(stored)
    assert await store.load(bound) == stored
    await redisClient.set(
        f"va:v1:bot:cb:{'C' * 16}", '{"action": "undo", "args": {}, "chat_id": "x"}', ex=60
    )
    assert await store.load("C" * 16) is None


# Примечание: учёт уведомлений J4: «в работе» SET NX с коротким TTL и токеном, «доставлено» —
# message_id на 24 ч; снять «в работе» можно только своим токеном
async def test_notificationLedgerKeys(redisClient: Redis, uniqueId: str) -> None:
    ledger = RedisNotificationLedger(KeyedRedis(redisClient))
    assert await ledger.deliveredMessage(uniqueId) is None
    token = await ledger.claim(uniqueId, 60)
    assert token is not None
    assert await ledger.claim(uniqueId, 60) is None
    lockTtl = await redisClient.ttl(f"va:v1:bot:notif_lock:{uniqueId}")
    assert 55 < lockTtl <= 60
    await ledger.release(uniqueId, "чужой")
    assert await redisClient.exists(f"va:v1:bot:notif_lock:{uniqueId}") == 1
    await ledger.markDelivered(uniqueId, "mid.1")
    await ledger.release(uniqueId, token)
    assert await redisClient.exists(f"va:v1:bot:notif_lock:{uniqueId}") == 0
    assert await ledger.deliveredMessage(uniqueId) == "mid.1"
    ttl = await redisClient.ttl(f"va:v1:bot:notif:{uniqueId}")
    assert NOTIFICATION_TTL_S - 5 < ttl <= NOTIFICATION_TTL_S
