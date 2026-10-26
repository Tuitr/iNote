"""RealMaxGateway на фейковом транспорте: запросы по схеме MAX Bot API и разбор ответов."""

import json
from collections.abc import AsyncIterator, Callable
from typing import Final

import httpx
import pytest

from bot.domain.messages import OutgoingMessage
from bot.domain.notifications import DeliveryFailure, MaxDeliveryError
from bot.infrastructure.real_max import MaxApiError, RealMaxGateway, createMaxHttp
from services.bot.tests.fakes import loadMaxUpdate

API_URL: Final = "https://max.test"
CHAT_ID: Final = 500600700

type Handler = Callable[[httpx.Request], httpx.Response]


class MaxApi:
    """Фейковый MAX: отвечает handler'ом и запоминает запросы."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


@pytest.fixture
async def http() -> AsyncIterator[Callable[[Handler], tuple[RealMaxGateway, MaxApi]]]:
    clients: list[httpx.AsyncClient] = []

    def build(handler: Handler) -> tuple[RealMaxGateway, MaxApi]:
        api = MaxApi(handler)
        client = createMaxHttp(
            "max-token",
            apiUrl=API_URL,
            timeoutS=5,
            verify=True,
            transport=httpx.MockTransport(api),
        )
        clients.append(client)
        return RealMaxGateway(client), api

    yield build
    for client in clients:
        await client.aclose()


# Примечание: сообщение — POST /messages?chat_id с телом NewMessageBody; id — message.body.mid
async def test_sendMessage(http: Callable[[Handler], tuple[RealMaxGateway, MaxApi]]) -> None:
    gateway, api = http(
        lambda _: httpx.Response(200, json={"message": {"body": {"mid": "mid.1", "text": "x"}}})
    )
    messageId = await gateway.sendMessage(OutgoingMessage(chatId=CHAT_ID, text="Привет"))
    [request] = api.requests
    assert messageId == "mid.1"
    assert (request.method, request.url.path) == ("POST", "/messages")
    assert request.url.params["chat_id"] == str(CHAT_ID)
    assert request.headers["Authorization"] == "max-token"
    assert json.loads(request.content) == {"text": "Привет", "attachments": []}


# Примечание: 403/404 — чат недоступен (повтор бесполезен), 429 — позже, 5xx и сеть — недоступен
@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (403, DeliveryFailure.CHAT_NOT_FOUND),
        (404, DeliveryFailure.CHAT_NOT_FOUND),
        (429, DeliveryFailure.RATE_LIMITED),
        (503, DeliveryFailure.UNAVAILABLE),
    ],
)
async def test_sendMessageFailures(
    http: Callable[[Handler], tuple[RealMaxGateway, MaxApi]],
    status: int,
    reason: DeliveryFailure,
) -> None:
    gateway, _ = http(lambda _: httpx.Response(status, json={"code": "x", "message": "y"}))
    with pytest.raises(MaxDeliveryError) as caught:
        await gateway.sendMessage(OutgoingMessage(chatId=CHAT_ID, text="Привет"))
    assert caught.value.reason is reason


async def test_sendMessageNetworkError(
    http: Callable[[Handler], tuple[RealMaxGateway, MaxApi]],
) -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    gateway, _ = http(broken)
    with pytest.raises(MaxDeliveryError) as caught:
        await gateway.sendMessage(OutgoingMessage(chatId=CHAT_ID, text="Привет"))
    assert caught.value.reason is DeliveryFailure.UNAVAILABLE


# Примечание: GET /updates с marker, timeout и types; апдейты разбирает max_mapper
async def test_fetchUpdates(http: Callable[[Handler], tuple[RealMaxGateway, MaxApi]]) -> None:
    raw = loadMaxUpdate("text")
    gateway, api = http(lambda _: httpx.Response(200, json={"updates": [raw], "marker": 42}))
    batch = await gateway.fetchUpdates(marker=41, timeoutS=30)
    [request] = api.requests
    assert (batch.marker, len(batch.updates)) == (42, 1)
    assert request.url.params["marker"] == "41"
    assert request.url.params["timeout"] == "30"
    assert request.url.params["types"] == "bot_started,message_callback,message_created"


# Примечание: ответ на кнопку с текстом — POST /answers; без текста запрос не нужен
async def test_answerCallback(http: Callable[[Handler], tuple[RealMaxGateway, MaxApi]]) -> None:
    gateway, api = http(lambda _: httpx.Response(200, json={"success": True}))
    await gateway.answerCallback("cb.1", None)
    await gateway.answerCallback("cb.1", "Слишком часто")
    [request] = api.requests
    assert request.url.params["callback_id"] == "cb.1"
    assert json.loads(request.content) == {"notification": "Слишком часто"}


# Примечание: «печатает…» — POST /chats/{id}/actions
async def test_sendTyping(http: Callable[[Handler], tuple[RealMaxGateway, MaxApi]]) -> None:
    gateway, api = http(lambda _: httpx.Response(200, json={"success": True}))
    await gateway.sendTyping(CHAT_ID)
    assert api.requests[0].url.path == f"/chats/{CHAT_ID}/actions"
    assert json.loads(api.requests[0].content) == {"action": "typing_on"}


# Примечание: MAX отказал в подписке — ошибка старта, а не бот без апдейтов
async def test_subscriptionRejected(
    http: Callable[[Handler], tuple[RealMaxGateway, MaxApi]],
) -> None:
    gateway, _ = http(lambda _: httpx.Response(200, json={"success": False, "message": "bad url"}))
    with pytest.raises(MaxApiError, match="bad url"):
        await gateway.ensureSubscription("https://bot.example/webhook/max", "secret_12345")
