"""POST /webhook/max: 200 до обработки, секрет подписки, дубли, режимы, ensureSubscription."""

import asyncio
import copy
import json
from collections.abc import AsyncIterator
from typing import Final, override

import fakeredis
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import ValidationError
from redis.asyncio import Redis

from bot.domain.updates import WEBHOOK_SECRET_HEADER
from bot.infrastructure.settings import BotSettings
from bot.main import createApp
from services.bot.tests.fakes import REPLY_EXAMPLES, loadMaxUpdate

SECRET: Final = "stand-secret_42"
HOOK_URL: Final = "http://bot/webhook/max"
HEADERS: Final = {WEBHOOK_SECRET_HEADER: SECRET}


# Примечание: окружение бота в режиме webhook с фейковым MAX
@pytest.fixture
def hookEnv(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("SERVICE_TOKEN", "t" * 32)
    monkeypatch.setenv("BOT_MAX_MODE", "fake")
    monkeypatch.setenv("BOT_UPDATES_MODE", "webhook")
    monkeypatch.setenv("BOT_WEBHOOK_URL", HOOK_URL)
    monkeypatch.setenv("BOT_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("BOT_SHUTDOWN_TIMEOUT_S", "3")
    return monkeypatch


# Примечание: fakeredis вместо настоящего Redis
def fakeRedisFactory(url: str) -> Redis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


# Примечание: агент, который отвечает только после gate.set() — видно, что 200 ушёл раньше
class GatedAgent(httpx.AsyncBaseTransport):
    # Примечание: gate открыт по умолчанию; requests — запросы к /v1/*
    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.gate.set()
        self.requests: list[httpx.Request] = []

    # Примечание: health — сразу; /v1/messages — confirm.json после gate
    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health/live":
            return httpx.Response(200, json={"status": "ok"})
        await request.aread()
        self.requests.append(request)
        await self.gate.wait()
        return httpx.Response(200, content=(REPLY_EXAMPLES / "confirm.json").read_bytes())


# Примечание: транспорт к самому боту — им фейковый MAX доставляет апдейты на webhook
class SelfTransport(httpx.AsyncBaseTransport):
    # Примечание: app появляется после createApp
    def __init__(self) -> None:
        self.app: FastAPI | None = None

    # Примечание: запрос в ASGI-приложение бота
    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert self.app is not None
        return await httpx.ASGITransport(app=self.app).handle_async_request(request)


# Примечание: фейковый агент на тест
@pytest.fixture
def agent() -> GatedAgent:
    return GatedAgent()


# Примечание: бот в режиме webhook; фейковый MAX стучится на свой же /webhook/max
@pytest.fixture
def app(hookEnv: pytest.MonkeyPatch, agent: GatedAgent) -> FastAPI:
    selfTransport = SelfTransport()
    created = createApp(
        BotSettings(),
        redisFactory=fakeRedisFactory,
        agentTransport=agent,
        webhookTransport=selfTransport,
    )
    selfTransport.app = created
    return created


# Примечание: клиент к приложению с запущенным lifespan
@pytest_asyncio.fixture
async def running(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bot") as client:
            yield client


# Примечание: дождаться n отправленных сообщений у фейкового MAX
async def waitSent(client: httpx.AsyncClient, count: int) -> list[dict[str, object]]:
    async with asyncio.timeout(3):
        while True:
            messages: list[dict[str, object]] = (await client.get("/dev/max/sent")).json()[
                "messages"
            ]
            if len(messages) >= count:
                return messages
            await asyncio.sleep(0.02)


# Примечание: 200 уходит, пока агент ещё думает; ответ пользователю — после
async def test_answersBeforeProcessing(running: httpx.AsyncClient, agent: GatedAgent) -> None:
    agent.gate.clear()
    response = await running.post("/webhook/max", json=loadMaxUpdate("text"), headers=HEADERS)
    assert (response.status_code, response.json()) == (200, {"ok": True})
    async with asyncio.timeout(2):
        while not agent.requests:  # noqa: ASYNC110 — ждём, пока обработка дошла до агента
            await asyncio.sleep(0.01)
    assert (await running.get("/dev/max/sent")).json()["messages"] == []
    agent.gate.set()
    [message] = await waitSent(running, 1)
    assert str(message["text"]).startswith("Напоминание создано")


# Примечание: без секрета или с чужим — 401 и ничего не обрабатывается
@pytest.mark.parametrize("headers", [{}, {WEBHOOK_SECRET_HEADER: "wrong-secret"}])
async def test_secretRequired(
    running: httpx.AsyncClient, agent: GatedAgent, headers: dict[str, str]
) -> None:
    response = await running.post("/webhook/max", json=loadMaxUpdate("text"), headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    await asyncio.sleep(0.05)
    assert agent.requests == []


# Примечание: MAX повторил тот же апдейт — один вызов агента и одно сообщение (дедуп)
async def test_duplicateHandledOnce(running: httpx.AsyncClient, agent: GatedAgent) -> None:
    raw = loadMaxUpdate("text")
    for _ in range(2):
        assert (await running.post("/webhook/max", json=raw, headers=HEADERS)).status_code == 200
    await waitSent(running, 1)
    await asyncio.sleep(0.1)
    assert len(agent.requests) == 1


# Примечание: перемешанные апдейты двух пользователей — у каждого агент видит свой порядок
async def test_orderPerUser(running: httpx.AsyncClient, agent: GatedAgent) -> None:
    for n in range(3):
        for userId in (101, 202):
            raw = copy.deepcopy(loadMaxUpdate("text"))
            message = raw["message"]
            assert isinstance(message, dict)
            message["sender"]["user_id"] = userId
            message["recipient"]["chat_id"] = userId
            message["body"]["mid"] = f"mid.{userId}.{n}"
            message["body"]["text"] = f"сообщение {n}"
            await running.post("/webhook/max", json=raw, headers=HEADERS)
    await waitSent(running, 6)
    seen: dict[int, list[str]] = {}
    for request in agent.requests:
        body = json.loads(request.content)
        seen.setdefault(body["max_user_id"], []).append(body["text"])
    assert seen == {u: [f"сообщение {n}" for n in range(3)] for u in (101, 202)}


# Примечание: битое тело или неподдержанный тип — 200 (иначе MAX будет повторять), без агента
@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'{"update_type": "message_edited"}'])
async def test_invalidBodyAccepted(
    running: httpx.AsyncClient, agent: GatedAgent, body: bytes
) -> None:
    response = await running.post("/webhook/max", content=body, headers=HEADERS)
    assert response.status_code == 200
    assert agent.requests == []


# Примечание: при старте бот подписывается; секрет в dev-журнале не показывается
async def test_subscribedOnStartup(running: httpx.AsyncClient) -> None:
    response = await running.get("/dev/max/subscriptions")
    assert response.json() == [
        {
            "url": HOOK_URL,
            "has_secret": True,
            "update_types": ["message_created", "message_callback", "bot_started"],
        }
    ]
    assert SECRET not in response.text


# Примечание: фейковый MAX в режиме webhook доставляет /dev/max/updates POST-ом с секретом
async def test_fakeDeliversViaWebhook(running: httpx.AsyncClient, agent: GatedAgent) -> None:
    await running.post("/dev/max/updates", json=loadMaxUpdate("text"))
    [message] = await waitSent(running, 1)
    assert str(message["text"]).startswith("Напоминание создано")
    assert len(agent.requests) == 1


# Примечание: в режиме polling webhook-роута нет — 404
async def test_pollingModeHasNoWebhook(hookEnv: pytest.MonkeyPatch, agent: GatedAgent) -> None:
    hookEnv.setenv("BOT_UPDATES_MODE", "polling")
    hookEnv.setenv("BOT_POLL_TIMEOUT_S", "1")
    app = createApp(BotSettings(), redisFactory=fakeRedisFactory, agentTransport=agent)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bot") as client:
            response = await client.post("/webhook/max", json={}, headers=HEADERS)
    assert response.status_code == 404


# Примечание: webhook без адреса или секрета, плохой секрет, http у настоящего MAX — не стартуем
@pytest.mark.parametrize(
    "patch",
    [
        {"BOT_WEBHOOK_URL": ""},
        {"BOT_WEBHOOK_SECRET": ""},
        {"BOT_WEBHOOK_SECRET": "abc"},
        {"BOT_WEBHOOK_SECRET": "with space"},
        {"BOT_MAX_MODE": "real", "BOT_WEBHOOK_URL": "http://stand.example/webhook/max"},
    ],
)
def test_webhookSettingsValidated(hookEnv: pytest.MonkeyPatch, patch: dict[str, str]) -> None:
    for name, value in patch.items():
        hookEnv.setenv(name, value)
    with pytest.raises(ValidationError):
        BotSettings()


# Примечание: тело больше 1 МБ — 400 и без обработки: и по Content-Length, и потоком без него
async def test_bodyTooLarge(running: httpx.AsyncClient, agent: GatedAgent) -> None:
    big = b'{"update_type": "message_created", "pad": "' + b"x" * (1024 * 1024) + b'"}'
    response = await running.post("/webhook/max", content=big, headers=HEADERS)
    assert response.status_code == 400

    # Примечание: тело кусками без Content-Length (chunked)
    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(17):
            yield b"x" * 65536

    streamed = await running.post("/webhook/max", content=chunks(), headers=HEADERS)
    assert streamed.status_code == 400
    assert agent.requests == []
