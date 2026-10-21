import json
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import ValidationError
from services.agent.tests.conftest import EXAMPLES, SERVICE_TOKEN

from agent.infrastructure.settings import AgentSettings
from agent.main import createApp

TRACE = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
AUTH = {"Authorization": f"Bearer {SERVICE_TOKEN}"}


# Примечание: окружение агента: токен и пути к примерам из корня репо
@pytest.fixture
def agentEnv(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("SERVICE_TOKEN", SERVICE_TOKEN)
    monkeypatch.setenv("AGENT_STUB_MESSAGE_REPLY", str(EXAMPLES / "reply" / "confirm.json"))
    monkeypatch.setenv("AGENT_STUB_ACTION_REPLY", str(EXAMPLES / "reply" / "list.json"))
    return monkeypatch


# Примечание: приложение агента
@pytest.fixture
def app(agentEnv: pytest.MonkeyPatch) -> FastAPI:
    return createApp(AgentSettings())


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


# Примечание: /v1/messages → фиксированный Reply с trace_id текущей цепочки
async def test_messageReturnsStubReply(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/v1/messages",
        json=example("message_request.json"),
        headers={**AUTH, "X-Trace-Id": TRACE},
    )
    assert response.status_code == 200
    body = response.json()
    assert body.pop("trace_id") == TRACE == response.headers["X-Trace-Id"]
    assert body == expectedReply("confirm.json")


# Примечание: /v1/actions → свой фиксированный Reply
async def test_actionReturnsStubReply(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/actions", json=example("action_request.json"), headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body.pop("trace_id") == response.headers["X-Trace-Id"]
    assert body == expectedReply("list.json")


# Примечание: без токена 401, с неверным 403 — на обоих путях
@pytest.mark.parametrize("path", ["/v1/messages", "/v1/actions"])
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


# Примечание: health без зависимостей — live и ready 200, токен не нужен
async def test_health(client: httpx.AsyncClient) -> None:
    assert (await client.get("/health/live")).status_code == 200
    ready = await client.get("/health/ready")
    assert ready.json() == {"status": "ok", "checks": {}}


# Примечание: пример ответа не по контракту — агент не стартует
async def test_invalidStubFailsStartup(agentEnv: pytest.MonkeyPatch, tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps({"text": "", "state": "done", "trace_id": TRACE}))
    agentEnv.setenv("AGENT_STUB_MESSAGE_REPLY", str(broken))
    app = createApp(AgentSettings())
    with pytest.raises(ValidationError):
        async with app.router.lifespan_context(app):
            pass
