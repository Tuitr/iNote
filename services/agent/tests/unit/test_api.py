import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

import fakeredis
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from agent.infrastructure.scripted_brain import HELP
from agent.infrastructure.settings import AgentSettings
from agent.main import AgentOverrides, createApp
from agent.presentation.templates import loadTemplates
from contracts.agent_side import ToolResult
from services.agent.tests.conftest import EXAMPLES, SERVICE_TOKEN
from services.agent.tests.fakes import ITEM_ID, FakeMcpHttp, RecordingToolFactory, mutation

TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
AUTH: Final = {"Authorization": f"Bearer {SERVICE_TOKEN}"}


# Примечание: окружение агента: токен и пути к примерам из корня репо
@pytest.fixture
def agentEnv(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("SERVICE_TOKEN", SERVICE_TOKEN)
    return monkeypatch


# Примечание: служебный REST mcp-контейнера (J8 resolve и health) — фейк
@pytest.fixture
def mcpHttp() -> FakeMcpHttp:
    return FakeMcpHttp(userId=17)


# Примечание: инструменты хода — фейк: delete_item удаляет запись из примеров
@pytest.fixture
def tools() -> RecordingToolFactory:
    return RecordingToolFactory({"delete_item": mutation("deleted")})


# Примечание: приложение агента: fakeredis, фейковый J8 и фейковые инструменты
@pytest.fixture
def app(agentEnv: pytest.MonkeyPatch, mcpHttp: FakeMcpHttp, tools: RecordingToolFactory) -> FastAPI:
    overrides = AgentOverrides(redisFactory=fakeRedis, mcpTransport=mcpHttp.transport, tools=tools)
    return createApp(AgentSettings(), overrides)


# Примечание: fakeredis вместо настоящего Redis
def fakeRedis(url: str) -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


# Примечание: клиент к агенту с запущенным lifespan
@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as c:
            yield c


# Примечание: пример запроса бота
def example(name: str) -> object:
    data: object = json.loads((EXAMPLES / "agent" / name).read_text())
    return data


# Примечание: пример ответа без trace_id (его подставляет агент)
def expectedReply(name: str) -> dict[str, object]:
    data: dict[str, object] = json.loads((EXAMPLES / "reply" / name).read_text())
    data.pop("trace_id")
    return data


# Примечание: /v1/messages идёт через ход агента; trace_id цепочки сохраняется в Reply
async def test_messageRunsTurn(client: httpx.AsyncClient) -> None:
    request = example("message_request.json")
    assert isinstance(request, dict)
    response = await client.post(
        "/v1/messages",
        json={**request, "text": "привет"},
        headers={**AUTH, "X-Trace-Id": TRACE},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["trace_id"] == TRACE == response.headers["X-Trace-Id"]
    assert body["state"] == "answer"
    assert body["text"] == HELP


# Примечание: пользователь определяется через J8 (trace цепочки уходит дальше), второй ход — из кеша
async def test_userResolvedViaJ8(client: httpx.AsyncClient, mcpHttp: FakeMcpHttp) -> None:
    request = example("message_request.json")
    assert isinstance(request, dict)
    for _ in range(2):
        response = await client.post(
            "/v1/messages",
            json={**request, "text": "привет"},
            headers={**AUTH, "X-Trace-Id": TRACE},
        )
        assert response.status_code == 200
    [resolve] = mcpHttp.resolves
    assert resolve.headers["X-Trace-Id"] == TRACE
    assert json.loads(resolve.content)["max_user_id"] == request["max_user_id"]


# Примечание: J8 недоступен — пользователь получает ответ с ошибкой и кодом, а не 5xx
async def test_userResolveDown(client: httpx.AsyncClient, mcpHttp: FakeMcpHttp) -> None:
    mcpHttp.down = True
    response = await client.post(
        "/v1/messages", json=example("message_request.json"), headers={**AUTH, "X-Trace-Id": TRACE}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "error"
    assert TRACE[:6] in body["text"]


# Примечание: /v1/actions: «Отменить» из примера бота → delete_item под пользователем J8
async def test_actionUndo(client: httpx.AsyncClient, tools: RecordingToolFactory) -> None:
    response = await client.post(
        "/v1/actions", json=example("action_request.json"), headers={**AUTH, "X-Trace-Id": TRACE}
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["trace_id"], body["state"]) == (TRACE, "done")
    assert body["text"].startswith(loadTemplates().changed.deleted)
    assert tools.calls == [("delete_item", {"id": ITEM_ID})]
    assert [c.userId for c in tools.callers] == [17]


# Примечание: незнакомое действие — 200 и «Не понял кнопку», а не 4xx (бот покажет текст)
async def test_unknownActionAnswered(client: httpx.AsyncClient) -> None:
    request = example("action_request.json")
    assert isinstance(request, dict)
    response = await client.post(
        "/v1/actions", json={**request, "action": "teleport"}, headers=AUTH
    )
    assert response.status_code == 200
    assert response.json()["text"] == loadTemplates().notices.unknownAction


# Примечание: без токена 401, с неверным 403 — на обоих путях
@pytest.mark.parametrize("path", ["/v1/messages", "/v1/actions", "/v1/focus"])
async def test_serviceTokenRequired(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.post(path, json={})).status_code == 401
    wrong = await client.post(path, json={}, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "forbidden"


# Примечание: запрос не по контракту → validation_error 400 без текста пользователя в ответе
async def test_invalidRequest(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/messages",
        json={"max_user_id": 1, "max_chat_id": 2, "text": "", "source": "telepathy"},
        headers=AUTH,
    )
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert {tuple(f["loc"]) for f in error["details"]["fields"]} >= {("body", "text")}


# Примечание: health: live и ready (Redis и mcp-контейнер) без токена
async def test_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health/live")).status_code == 200
    ready = await client.get("/health/ready")
    assert ready.json() == {"status": "ok", "checks": {"redis": "ok", "mcp": "ok"}}


# Примечание: mcp-контейнер не отвечает — агент не готов (503), Redis при этом ok
async def test_notReadyWithoutMcp(client: httpx.AsyncClient, mcpHttp: FakeMcpHttp) -> None:
    mcpHttp.healthy = False
    ready = await client.get("/health/ready")
    assert ready.status_code == 503
    assert ready.json()["checks"] == {"redis": "ok", "mcp": "fail"}


# Примечание: AGENT_BRAIN=llm с LLM_PROVIDER=fake — агент стартует с Brain B без сети и денег;
# у фейка нет ответов, поэтому ход — ошибка с кодом, а не 5xx
async def test_llmBrainStarts(
    agentEnv: pytest.MonkeyPatch, mcpHttp: FakeMcpHttp, tools: RecordingToolFactory
) -> None:
    agentEnv.setenv("AGENT_BRAIN", "llm")
    agentEnv.setenv("LLM_PROVIDER", "fake")
    overrides = AgentOverrides(redisFactory=fakeRedis, mcpTransport=mcpHttp.transport, tools=tools)
    app = createApp(AgentSettings(), overrides)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as c:
            response = await c.post(
                "/v1/messages",
                json={**example("message_request.json"), "text": "привет"},  # type: ignore[dict-item]
                headers={**AUTH, "X-Trace-Id": TRACE},
            )
    assert response.status_code == 200
    assert response.json()["state"] == "error"


# Примечание: ход через HTTP пишет историю J8 в фоне: тот же trace_id, внутренний user_id;
# при остановке агента запись дописывается до закрытия клиента
async def test_dialogTurnsWrittenInBackground(app: FastAPI, mcpHttp: FakeMcpHttp) -> None:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
            response = await client.post(
                "/v1/actions",
                json=example("action_request.json"),
                headers={**AUTH, "X-Trace-Id": TRACE},
            )
    assert response.status_code == 200
    [written] = mcpHttp.dialogTurns
    body = json.loads(written.content)
    assert (body["user_id"], body["trace_id"]) == (17, TRACE)
    assert [t["role"] for t in body["turns"]] == ["user", "assistant"]
    assert body["turns"][0] == {"role": "user", "text": "Отменить", "input_type": "text"}
    assert written.headers["Idempotency-Key"] == f"dialog-turns:{TRACE}"


# Примечание: S3 — бот доставил напоминание → /v1/focus (204, ключ фокуса с TTL 2 ч) →
# «перенеси его на завтра в 15» двигает именно эту запись, без вопросов
async def test_focusThenMoveIt(app: FastAPI, tools: RecordingToolFactory) -> None:
    tools.responses["update_item"] = mutation("updated")
    focus = example("focus_request.json")
    assert isinstance(focus, dict)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent") as client:
            set_ = await client.post("/v1/focus", json=focus, headers=AUTH)
            assert set_.status_code == 204
            assert set_.content == b""
            redis = app.state.redis
            ttl = await redis.ttl("va:v1:agent:focus:17")
            assert 2 * 60 * 60 - 5 <= ttl <= 2 * 60 * 60
            message = {**focus, "text": "перенеси его на завтра в 15", "source": "text"}
            del message["item"]
            response = await client.post("/v1/messages", json=message, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["state"] == "done"
    [(name, args)] = tools.calls
    assert (name, args["id"]) == ("update_item", focus["item"]["id"])
    assert set(args) == {"id", "remind_at"}


# Примечание: /v1/focus не по контракту (нет item) — 400, фокус не трогаем
async def test_focusInvalid(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/focus", json={"max_user_id": 1, "max_chat_id": 2}, headers=AUTH
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


# Примечание: S3 — /settings через HTTP: set_tz → J8 PATCH, кеш пользователя в Redis сброшен,
# следующий «сегодня» считает день по новому поясу (resolve снова идёт в J8)
async def test_setTzThroughApi(
    client: httpx.AsyncClient, mcpHttp: FakeMcpHttp, tools: RecordingToolFactory
) -> None:
    tools.responses["list_items"] = ToolResult(ok=True, summary="ok", data={"items": []})
    base = {"max_user_id": 100200300, "max_chat_id": 500600700, "label": None}
    today = {**base, "action": "list_period", "args": {"period": "today"}}
    assert (await client.post("/v1/actions", json=today, headers=AUTH)).status_code == 200
    change = {**base, "action": "set_tz", "args": {"tz": "Asia/Kamchatka", "user_id": 1}}
    reply = (await client.post("/v1/actions", json=change, headers=AUTH)).json()
    assert reply["text"] == "Пояс: Камчатка (UTC+12)"
    [patch] = mcpHttp.settingsPatches
    assert patch.url.path == "/internal/v1/users/17/settings"
    assert (await client.post("/v1/actions", json=today, headers=AUTH)).status_code == 200
    assert len(mcpHttp.resolves) == 2
    days = [args["from"] for name, args in tools.calls if name == "list_items"]
    kamchatka = datetime.now(ZoneInfo("Asia/Kamchatka")).date().isoformat()
    assert days[-1] == kamchatka
