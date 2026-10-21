import asyncio
import json
from collections.abc import AsyncIterator
from typing import Final

import fakeredis
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import TypeAdapter
from redis.asyncio import Redis

from bot.infrastructure.real_max import ADAPTER_NOT_CHOSEN, MaxAdapterNotChosenError
from bot.infrastructure.settings import BotSettings
from bot.main import createApp
from bot.presentation import templates
from bot.presentation.dev_routes import SentLog, SentMessageDto
from common.redis import createRedis
from services.bot.tests.fakes import REPLY_EXAMPLES, loadMaxUpdate

BUTTONS: Final = TypeAdapter(list[list[dict[str, str]]])


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


# Примечание: агент на MockTransport: confirm.json на /v1/messages, list.json на /v1/actions
class FakeAgentHttp:
    # Примечание: requests — что бот отправил агенту
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down = False

    # Примечание: транспорт для createApp(agentTransport=…)
    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # Примечание: ответ-пример по пути; down — 503 в конверте ошибки
    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            error = {"code": "upstream_unavailable", "message": "down", "details": {}}
            return httpx.Response(503, json={"error": error})
        name = "confirm.json" if request.url.path == "/v1/messages" else "list.json"
        return httpx.Response(200, content=(REPLY_EXAMPLES / name).read_bytes())


# Примечание: фейковый агент на тест
@pytest.fixture
def agentHttp() -> FakeAgentHttp:
    return FakeAgentHttp()


# Примечание: бот целиком поверх fakeredis и фейкового агента
@pytest.fixture
def app(botEnv: pytest.MonkeyPatch, agentHttp: FakeAgentHttp) -> FastAPI:
    return createApp(
        BotSettings(), redisFactory=fakeRedisFactory, agentTransport=agentHttp.transport
    )


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


# Примечание: журнал фейкового MAX как модель — поля типизированы
async def sentLog(client: httpx.AsyncClient, count: int) -> SentLog:
    return SentLog.fromWire(await waitSent(client, count))


# Примечание: кнопки первого вложения inline_keyboard
def keyboardOf(message: SentMessageDto) -> list[list[dict[str, str]]]:
    [keyboard] = message.attachments
    payload = keyboard["payload"]
    assert isinstance(payload, dict)
    return BUTTONS.validate_python(payload["buttons"])


# Примечание: апдейт → polling → агент → Reply в формате MAX; нажатие кнопки → /v1/actions
async def test_textAndButtonThroughAgent(
    running: httpx.AsyncClient, agentHttp: FakeAgentHttp
) -> None:
    pushed = await running.post("/dev/max/updates", json=loadMaxUpdate("text"))
    assert pushed.json() == {"accepted": True, "position": 0}
    log = await sentLog(running, 1)
    [message] = log.messages
    assert message.text.startswith("Напоминание создано")
    assert message.format == "markdown"
    undo = keyboardOf(message)[0][0]
    assert (undo["type"], undo["text"]) == ("callback", "Отменить")
    assert log.typing == [500600700]
    [request] = agentHttp.requests
    assert json.loads(request.content)["name"] == "Иван"
    # Нажатие «Отменить»: payload — ключ в Redis, за ним action=undo
    press = loadMaxUpdate("callback")
    callback = press["callback"]
    assert isinstance(callback, dict)
    callback["payload"] = undo["payload"]
    await running.post("/dev/max/updates", json=press)
    log = await sentLog(running, 2)
    action = json.loads(agentHttp.requests[1].content)
    assert (action["action"], action["callback_id"]) == ("undo", "cb.synthetic.0001")
    [answer] = log.callbackAnswers
    assert (answer.callbackId, answer.notification) == ("cb.synthetic.0001", None)


# Примечание: агент лежит — пользователь получает текст бота с кодом цепочки
async def test_agentDown(running: httpx.AsyncClient, agentHttp: FakeAgentHttp) -> None:
    agentHttp.down = True
    await running.post("/dev/max/updates", json=loadMaxUpdate("text"))
    [message] = (await sentLog(running, 1)).messages
    assert message.text.startswith("Не получилось связаться с ассистентом")
    assert message.attachments == []


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
