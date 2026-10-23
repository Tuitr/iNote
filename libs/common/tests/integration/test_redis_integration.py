import httpx
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis

from common.health import buildHealthRouter, redisHealthCheck
from common.redis import KeyedRedis, RedisKey, createRedis

pytestmark = pytest.mark.integration


# Примечание: readiness с настоящим Redis — 200 ok
async def test_readyWithRealRedis(redisClient: Redis) -> None:
    app = FastAPI()
    app.include_router(buildHealthRouter({"redis": redisHealthCheck(redisClient)}))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"redis": "ok"}}


# Примечание: Redis недоступен → 503 degraded в пределах таймаута проверки
async def test_readyDegradedWhenRedisDown() -> None:
    dead = createRedis("redis://127.0.0.1:1/0", socketTimeoutS=0.2)
    app = FastAPI()
    app.include_router(buildHealthRouter({"redis": redisHealthCheck(dead, timeoutS=0.5)}))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/ready")
    await dead.aclose()
    assert response.status_code == 503
    assert response.json()["checks"]["redis"] in {"fail", "timeout"}


# Примечание: настоящий Redis: SET NX EX и TTL окна счётчика
async def test_ttlOnRealRedis(redisClient: Redis, uniqueId: str) -> None:
    store = KeyedRedis(redisClient)
    updKey = RedisKey("bot", "upd", uniqueId)
    assert await store.setIfAbsent(updKey, "1", ttlS=86400) is True
    assert await store.setIfAbsent(updKey, "1", ttlS=86400) is False
    assert 86000 < await store.ttl(updKey) <= 86400
    rlKey = RedisKey("bot", "rl", uniqueId)
    for expected in (1, 2, 3):
        assert await store.incrementWithTtl(rlKey, ttlS=60) == expected
    assert 0 < await store.ttl(rlKey) <= 60
