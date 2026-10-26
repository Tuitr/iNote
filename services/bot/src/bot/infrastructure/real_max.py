"""Настоящий MAX (BOT_MAX_MODE=real): MAX Bot API по HTTPS напрямую через httpx, без SDK.

По схеме max-messenger/api-schema: адрес https://platform-api2.max.ru, токен — в заголовке
Authorization (query-параметр access_token больше не принимается). Сертификат API выпущен НУЦ
Минцифры — путь к нему задаётся BOT_MAX_CA_BUNDLE. Отправка сообщения не повторяется: повтор
POST /messages после таймаута может задвоить сообщение; повторяет вызывающий (Scheduler — по J4).
Текст сообщений и токен в лог не пишем.
"""

from collections.abc import Mapping
from typing import Final

import httpx

from bot.domain.messages import MessageId, OutgoingMessage
from bot.domain.notifications import DeliveryFailure, MaxDeliveryError
from bot.domain.updates import MaxChatId, UpdateBatch
from bot.infrastructure.max_mapper import SUPPORTED_TYPES, mapRawUpdate
from bot.infrastructure.max_outgoing import toNewMessageBody, typingBody
from common.logging import getLogger

MAX_API_URL: Final = "https://platform-api2.max.ru"
# Типы апдейтов, которые бот разбирает (max_mapper)
UPDATE_TYPES: Final = tuple(sorted(SUPPORTED_TYPES))
UPDATES_LIMIT: Final = 100
# long polling держит соединение timeoutS секунд — HTTP-таймаут должен быть больше
POLL_GRACE_S: Final = 10.0
# 403 — пользователь остановил бота или нет доступа к чату, 404 — чата нет: повтор бесполезен
_GONE: Final = frozenset({httpx.codes.FORBIDDEN, httpx.codes.NOT_FOUND})

_log = getLogger(__name__)


# Примечание: MAX не принял служебный запрос (подписка, апдейты) — ошибка старта или цикла polling
class MaxApiError(RuntimeError):
    pass


# Примечание: адаптер MaxGateway к MAX Bot API; http (адрес, токен, сертификат) создаёт lifespan
class RealMaxGateway:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    # Примечание: GET /updates: marker=None — всё, что ещё не подтверждено; неразобранные — пропуск
    async def fetchUpdates(self, marker: int | None, timeoutS: int) -> UpdateBatch:
        params: dict[str, str | int] = {
            "limit": UPDATES_LIMIT,
            "timeout": timeoutS,
            "types": ",".join(UPDATE_TYPES),
        }
        if marker is not None:
            params["marker"] = marker
        response = await self._http.get("/updates", params=params, timeout=timeoutS + POLL_GRACE_S)
        payload = _json(response, "updates")
        raws = payload.get("updates")
        items = raws if isinstance(raws, list) else []
        updates = tuple(
            update
            for raw in items
            if isinstance(raw, Mapping) and (update := mapRawUpdate(raw)) is not None
        )
        nextMarker = payload.get("marker")
        return UpdateBatch(
            updates=updates, marker=nextMarker if isinstance(nextMarker, int) else marker
        )

    # Примечание: POST /messages?chat_id=…; ответ — message.body.mid; отказ — MaxDeliveryError
    async def sendMessage(self, message: OutgoingMessage) -> MessageId:
        try:
            response = await self._http.post(
                "/messages", params={"chat_id": message.chatId}, json=toNewMessageBody(message)
            )
        except httpx.HTTPError as exc:
            raise MaxDeliveryError(DeliveryFailure.UNAVAILABLE, type(exc).__name__) from exc
        status = response.status_code
        if status in _GONE:
            raise MaxDeliveryError(DeliveryFailure.CHAT_NOT_FOUND, str(status))
        if status == httpx.codes.TOO_MANY_REQUESTS:
            raise MaxDeliveryError(DeliveryFailure.RATE_LIMITED, str(status))
        if status != httpx.codes.OK:
            # 400 — ошибка в теле сообщения у нас; в лог, чтобы не прятать её за «недоступен»
            _log.warning("max_message_rejected", status=status, error=_errorCode(response))
            raise MaxDeliveryError(DeliveryFailure.UNAVAILABLE, str(status))
        messageId = _messageId(response)
        _log.info("max_message_sent", message_id=messageId)
        return messageId

    # Примечание: POST /answers?callback_id=…: всплывающее уведомление; без текста MAX нечего
    # показывать — запрос не шлём. Ответ на нажатие вспомогательный: сбой — в лог, ход идёт дальше
    async def answerCallback(self, callbackId: str, notification: str | None) -> None:
        if notification is None:
            return
        try:
            response = await self._http.post(
                "/answers", params={"callback_id": callbackId}, json={"notification": notification}
            )
        except httpx.HTTPError as exc:
            _log.warning("max_callback_answer_failed", error=type(exc).__name__)
            return
        if response.status_code != httpx.codes.OK:
            _log.warning("max_callback_answer_failed", status=response.status_code)
            return
        _log.info("max_callback_answered")

    # Примечание: POST /chats/{chat_id}/actions typing_on; сбой ловит TypingIndicator
    async def sendTyping(self, chatId: MaxChatId) -> None:
        response = await self._http.post(f"/chats/{chatId}/actions", json=typingBody())
        response.raise_for_status()

    # Примечание: POST /subscriptions при каждом старте: MAX отписывает бота после 8 ч без 200;
    # отказ — ошибка старта, иначе бот молча не получит ни одного апдейта
    async def ensureSubscription(self, url: str, secret: str) -> None:
        body = {"url": url, "secret": secret, "update_types": list(UPDATE_TYPES)}
        payload = _json(await self._http.post("/subscriptions", json=body), "subscriptions")
        if payload.get("success") is not True:
            msg = f"MAX не принял подписку webhook: {payload.get('message') or 'без причины'}"
            raise MaxApiError(msg)
        _log.info("max_subscribed", update_types=list(UPDATE_TYPES))


# Примечание: HTTP-клиент MAX: адрес, токен в Authorization, сертификат Минцифры, таймаут
def createMaxHttp(
    token: str,
    *,
    apiUrl: str,
    timeoutS: float,
    verify: str | bool,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=apiUrl,
        headers={"Authorization": token},
        timeout=timeoutS,
        verify=verify,
        transport=transport,
    )


def _json(response: httpx.Response, method: str) -> dict[str, object]:
    if response.status_code != httpx.codes.OK:
        msg = f"MAX {method}: HTTP {response.status_code} {_errorCode(response)}"
        raise MaxApiError(msg)
    payload = response.json()
    if not isinstance(payload, dict):
        msg = f"MAX {method}: ответ не объект"
        raise MaxApiError(msg)
    return payload


# Примечание: код ошибки MAX ({"code": …, "message": …}) — в лог вместо тела ответа
def _errorCode(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return ""
    return str(payload.get("code") or "") if isinstance(payload, dict) else ""


def _messageId(response: httpx.Response) -> MessageId:
    payload = response.json()
    message = payload.get("message") if isinstance(payload, dict) else None
    body = message.get("body") if isinstance(message, dict) else None
    mid = body.get("mid") if isinstance(body, dict) else None
    if not isinstance(mid, str):
        raise MaxDeliveryError(DeliveryFailure.UNAVAILABLE, "ответ без message.body.mid")
    return mid
