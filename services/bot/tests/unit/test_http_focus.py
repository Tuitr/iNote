"""HttpFocusNotifier: POST /v1/focus агента фоном; ответ J4 не ждёт, сбой — warning без названия."""

import json
from typing import Final

import httpx
import structlog
from pydantic import SecretStr

from bot.infrastructure.http_focus import HttpFocusNotifier
from common.http import ServiceHttpClient
from common.lifecycle import TaskRegistry
from common.trace import traceContext
from services.bot.tests.fakes import notifyRequest

TOKEN: Final = "t" * 32
TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"


# Примечание: агент на MockTransport со статусом ответа; requests — что пришло
class Agent:
    # Примечание: status — код ответа на /v1/focus
    def __init__(self, status: int = 204) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    # Примечание: notifier с реестром задач
    def notifier(self, tasks: TaskRegistry) -> HttpFocusNotifier:
        http = ServiceHttpClient(
            baseUrl="http://agent:8000",
            timeoutS=45,
            serviceToken=SecretStr(TOKEN),
            upstreamName="agent",
            transport=httpx.MockTransport(self._handle),
        )
        return HttpFocusNotifier(http, tasks)

    # Примечание: записать запрос и ответить
    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status)


# Примечание: тело FocusRequest (поля записи из NotifyItem), токен и trace цепочки J4
async def test_sendsFocus() -> None:
    agent, tasks = Agent(), TaskRegistry()
    with traceContext(TRACE):
        agent.notifier(tasks).itemDelivered(notifyRequest())
    await tasks.shutdown()
    [request] = agent.requests
    assert request.url.path == "/v1/focus"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["X-Trace-Id"] == TRACE
    body = json.loads(request.content)
    assert (body["max_user_id"], body["max_chat_id"]) == (100200300, 500600700)
    assert body["item"]["id"] == "7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90"
    assert body["item"]["kind"] == "reminder"
    assert body["item"]["remind_at"] is not None


# Примечание: агент ответил 503 — только warning с кодом, без названия записи
async def test_failureOnlyLogged() -> None:
    agent, tasks = Agent(503), TaskRegistry()
    with structlog.testing.capture_logs() as logs:
        agent.notifier(tasks).itemDelivered(notifyRequest())
        await tasks.shutdown()
    assert [e["event"] for e in logs if e["log_level"] == "warning"] == ["focus_failed"]
    assert "Позвонить" not in str(logs)


# Примечание: сервис останавливается — вызов не ставится, warning focus_lost
async def test_shuttingDown() -> None:
    agent, tasks = Agent(), TaskRegistry()
    await tasks.shutdown()
    with structlog.testing.capture_logs() as logs:
        agent.notifier(tasks).itemDelivered(notifyRequest())
    assert agent.requests == []
    assert [e["event"] for e in logs] == ["focus_lost"]
