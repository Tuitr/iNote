import asyncio
from collections.abc import AsyncIterator

import fakeredis
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from redis.asyncio import Redis

from bot.infrastructure.real_max import ADAPTER_NOT_CHOSEN, MaxAdapterNotChosenError
from bot.infrastructure.settings import BotSettings
from bot.main import createApp
from bot.presentation import templates
from common.redis import createRedis
from services.bot.tests.fakes import loadMaxUpdate


# Примечание: окружение бота для тестов: фейковый MAX, короткий long polling
@pytest.fixture
def botEnv(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("SERVICE_TOKEN", "t" * 32)
    monkeypatch.setenv("BOT_MAX_MODE", "fake")
    monkeypatch.setenv("BOT_POLL_TIMEOUT_S", "1")
    monkeypatch.setenv("BOT_SHUTDOWN_TIMEOUT_S", "2")
    return monkeypatch


# Примечание: fakeredis вместо настоящего Redis
def fakeRedisFactory(url: str) -> Redis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


# Примечание: клиент к приложению с запущенным lifespan (polling, Redis) без сети
@pytest_asyncio.fixture
async def running(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bot") as client:
            yield client


# Примечание: бот целиком поверх fakeredis
@pytest.fixture
def app(botEnv: pytest.MonkeyPatch) -> FastAPI:
    return createApp(BotSettings(), redisFactory=fakeRedisFactory)


# Примечание: ждём, пока фейковый MAX «отправит» нужное число сообщений
async def waitSent(client: httpx.AsyncClient, count: int) -> dict[str, object]:
    async with asyncio.timeout(3):
        while True:
            body: dict[str, object] = (await client.get("/dev/max/sent")).json()
            messages = body["messages"]
            assert isinstance(messages, list)
            if len(messages) >= count:
                return body
            await asyncio.sleep(0.02)


# Примечание: путь апдейт → polling → HandleUpdate → фейковый MAX; JSON в snake_case
async def test_echoThroughDevRoutes(running: httpx.AsyncClient) -> None:
    pushed = await running.post("/dev/max/updates", json=loadMaxUpdate("text"))
    assert pushed.status_code == 200
    assert pushed.json() == {"accepted": True, "position": 0}
    body = await waitSent(running, 1)
    messages = body["messages"]
    assert isinstance(messages, list)
    [message] = messages
    assert isinstance(message, dict)
    assert message["text"] == "Вы написали: Напомни завтра в 10 позвонить Ивану"
    assert message["chat_id"] == 500600700
    assert body["callback_answers"] == []


# Примечание: один и тот же апдейт дважды — одно приветствие (дедуп через Redis)
async def test_duplicatePushHandledOnce(running: httpx.AsyncClient) -> None:
    raw = loadMaxUpdate("bot_started")
    await running.post("/dev/max/updates", json=raw)
    await running.post("/dev/max/updates", json=raw)
    await running.post("/dev/max/updates", json=loadMaxUpdate("voice"))
    body = await waitSent(running, 2)
    messages = body["messages"]
    assert isinstance(messages, list)
    assert [m["text"] for m in messages] == [templates.GREETING, templates.VOICE_NOT_YET]


# Примечание: health: live и ready (Redis отвечает)
async def test_health(running: httpx.AsyncClient) -> None:
    assert (await running.get("/health/live")).json() == {"status": "ok"}
    ready = await running.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"redis": "ok"}


# Примечание: Redis недоступен — ready 503, live по-прежнему 200
async def test_readyDegradedWithoutRedis(botEnv: pytest.MonkeyPatch) -> None:
    dead = createApp(
        BotSettings(),
        redisFactory=lambda _: createRedis("redis://127.0.0.1:1/0", socketTimeoutS=0.2),
    )
    async with dead.router.lifespan_context(dead):
        transport = httpx.ASGITransport(app=dead)
        async with httpx.AsyncClient(transport=transport, base_url="http://bot") as client:
            assert (await client.get("/health/live")).status_code == 200
            ready = await client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["status"] == "degraded"


# Примечание: real без выбранного адаптера — понятная ошибка на старте, dev-роутов нет
async def test_realModeFailsFast(botEnv: pytest.MonkeyPatch) -> None:
    botEnv.setenv("BOT_MAX_MODE", "real")
    real = createApp(BotSettings(), redisFactory=fakeRedisFactory)
    paths = {getattr(route, "path", "") for route in real.routes}
    assert not any(p.startswith("/dev/") for p in paths)
    with pytest.raises(MaxAdapterNotChosenError, match="maxapi"):
        async with real.router.lifespan_context(real):
            pass
    assert "открытое решение" in ADAPTER_NOT_CHOSEN
