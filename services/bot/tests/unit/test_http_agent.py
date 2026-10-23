"""HttpAgentGateway: POST в агента с токеном и trace, 45 с без повторов, ошибки — AppError."""

import json
from typing import Final

import httpx
import pytest
from pydantic import SecretStr

from bot.infrastructure.http_agent import HttpAgentGateway
from common.errors import AppError
from common.http import ServiceHttpClient
from common.trace import traceContext
from contracts.reply import ActionRequest, MessageRequest
from services.bot.tests.fakes import REPLY_EXAMPLES

TOKEN: Final = "t" * 32
TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
MESSAGE: Final = MessageRequest(maxUserId=1, maxChatId=2, text="что у меня сегодня", name="Иван")


# Примечание: агент на MockTransport; requests — что пришло
class Agent:
    # Примечание: respond — ответ на каждый запрос
    def __init__(self, respond: httpx.Response | Exception) -> None:
        self.respond = respond
        self.requests: list[httpx.Request] = []

    # Примечание: клиент бота к этому агенту
    def gateway(self) -> HttpAgentGateway:
        http = ServiceHttpClient(
            baseUrl="http://agent:8000",
            timeoutS=45,
            serviceToken=SecretStr(TOKEN),
            upstreamName="agent",
            transport=httpx.MockTransport(self._handle),
        )
        return HttpAgentGateway(http)

    # Примечание: записать запрос и ответить
    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(self.respond, Exception):
            raise self.respond
        return self.respond


# Примечание: ответ-пример Reply
def okReply(name: str = "list.json") -> httpx.Response:
    return httpx.Response(200, content=(REPLY_EXAMPLES / name).read_bytes())


# Примечание: /v1/messages: тело в snake_case, сервисный токен и trace цепочки бота
async def test_sendMessage() -> None:
    agent = Agent(okReply())
    with traceContext(TRACE):
        reply = await agent.gateway().sendMessage(MESSAGE)
    assert reply.state.value == "answer"
    [request] = agent.requests
    assert request.url.path == "/v1/messages"
    assert json.loads(request.content)["max_user_id"] == 1
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["X-Trace-Id"] == TRACE


# Примечание: /v1/actions
async def test_sendAction() -> None:
    agent = Agent(okReply("confirm.json"))
    request = ActionRequest(maxUserId=1, maxChatId=2, action="undo", args={"item_id": "x"})
    await agent.gateway().sendAction(request)
    assert agent.requests[0].url.path == "/v1/actions"
    assert json.loads(agent.requests[0].content)["args"] == {"item_id": "x"}


# Примечание: таймаут и 5xx не повторяются (стандарт: бот → агент без повторов)
@pytest.mark.parametrize(
    ("respond", "code"),
    [
        (httpx.ReadTimeout("slow"), "upstream_timeout"),
        (httpx.ConnectError("down"), "upstream_unavailable"),
        (
            httpx.Response(503, json={"error": {"code": "upstream_unavailable", "message": "x"}}),
            "upstream_unavailable",
        ),
    ],
    ids=["timeout", "connect", "503"],
)
async def test_noRetries(respond: httpx.Response | Exception, code: str) -> None:
    agent = Agent(respond)
    with pytest.raises(AppError) as caught:
        await agent.gateway().sendMessage(MESSAGE)
    assert caught.value.code == code
    assert len(agent.requests) == 1


# Примечание: 200, но не Reply — internal: бот не отправит в MAX мусор
async def test_badReply() -> None:
    agent = Agent(httpx.Response(200, json={"text": ""}))
    with pytest.raises(AppError) as caught:
        await agent.gateway().sendMessage(MESSAGE)
    assert caught.value.code == "internal"
