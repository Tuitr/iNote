import asyncio
import time

import fakeredis
import httpx
from fastapi import FastAPI

from common.health import HealthCheck, buildHealthRouter, redisHealthCheck


# Примечание: клиент к приложению только с health-роутером
def healthClient(checks: dict[str, HealthCheck]) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(buildHealthRouter(checks))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# Примечание: проверка, которая всегда успешна
async def okProbe() -> object:
    return True


# Примечание: проверка, которая падает
async def failingProbe() -> object:
    msg = "redis down"
    raise ConnectionError(msg)


# Примечание: проверка, которая висит дольше таймаута
async def hangingProbe() -> object:
    await asyncio.sleep(5)
    return True


# Примечание: live всегда 200 и не трогает зависимости
async def test_liveIgnoresChecks() -> None:
    async with healthClient({"redis": HealthCheck(failingProbe)}) as client:
        response = await client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# Примечание: все зависимости в порядке → 200 ok со списком проверок
async def test_readyOk() -> None:
    async with healthClient({"redis": HealthCheck(okProbe)}) as client:
        response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"redis": "ok"}}


# Примечание: одна упала, другая зависла → 503 degraded, у каждой свой статус
async def test_readyDegraded() -> None:
    checks = {
        "redis": HealthCheck(failingProbe),
        "agent": HealthCheck(hangingProbe, timeoutS=0.05),
        "self": HealthCheck(okProbe),
    }
    async with healthClient(checks) as client:
        started = time.perf_counter()
        response = await client.get("/health/ready")
        elapsed = time.perf_counter() - started
    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"redis": "fail", "agent": "timeout", "self": "ok"},
    }
    # Таймаут проверки соблюдён — readiness не висит на зависшем соседе
    assert elapsed < 1.0


# Примечание: готовая проверка Redis — PING на fakeredis
async def test_redisHealthCheckWithFake() -> None:
    redis = fakeredis.FakeAsyncRedis()
    async with healthClient({"redis": redisHealthCheck(redis)}) as client:
        response = await client.get("/health/ready")
    assert response.json()["checks"] == {"redis": "ok"}
    await redis.aclose()
