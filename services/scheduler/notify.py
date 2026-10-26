"""HTTP-клиент J4: Scheduler → бот, POST /internal/v1/notify.

Контракт J4 принадлежит C. Текст и кнопки строит бот, формата MAX здесь нет.
"""

import logging
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import AwareDatetime, BaseModel

from libs.contracts.items import Item
from libs.core.services.reminders import Delivery, DueReminder

logger = logging.getLogger(__name__)

NOTIFY_PATH = "/internal/v1/notify"
# Бот отвечает сразу, а в MAX отправляет у себя: 10 секунд хватает с запасом.
NOTIFY_TIMEOUT_SECONDS = 10.0


class NotifyUser(BaseModel):
    max_user_id: int
    max_chat_id: int
    tz: str


class NotifyRequest(BaseModel):
    notification_id: str
    # Встречу или задачу бот различает по item.kind, поэтому вид уведомления один.
    kind: Literal["reminder"] = "reminder"
    user: NotifyUser
    item: Item
    # Исходное время напоминания, даже если оно опоздало: бот решает, писать ли «с опозданием».
    fire_at: AwareDatetime
    attempt: int


class BotNotifier:
    def __init__(self, client: httpx.AsyncClient, service_token: str) -> None:
        """client уже знает адрес бота и таймаут; в тестах ему подставляют MockTransport."""
        self._client = client
        self._service_token = service_token

    async def notify(self, reminder: DueReminder) -> tuple[Delivery, str]:
        """Итог попытки и причина неудачи (пустая строка, если доставлено)."""
        trace_id = uuid4().hex
        try:
            response = await self._client.post(
                NOTIFY_PATH,
                content=_body(reminder),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self._service_token}",
                    "X-Trace-Id": trace_id,
                    "Idempotency-Key": str(reminder.notification_id),
                },
            )
        except httpx.TimeoutException:
            return _log(reminder, trace_id, Delivery.RETRY, "timeout")
        except httpx.TransportError as error:
            return _log(reminder, trace_id, Delivery.RETRY, f"transport: {type(error).__name__}")
        delivery, reason = _outcome(response)
        return _log(reminder, trace_id, delivery, reason)


def _body(reminder: DueReminder) -> str:
    return NotifyRequest(
        notification_id=str(reminder.notification_id),
        user=NotifyUser(
            max_user_id=reminder.max_user_id, max_chat_id=reminder.max_chat_id, tz=reminder.tz
        ),
        item=reminder.item,
        fire_at=reminder.fire_at,
        attempt=reminder.attempt,
    ).model_dump_json()


def _outcome(response: httpx.Response) -> tuple[Delivery, str]:
    status = response.status_code
    if status == httpx.codes.OK:
        if _json_field(response, "delivered") is True:
            return Delivery.SENT, ""
        return Delivery.RETRY, "200 without delivered: true"
    if status == httpx.codes.TOO_MANY_REQUESTS or status >= httpx.codes.INTERNAL_SERVER_ERROR:
        return Delivery.RETRY, f"HTTP {status}"
    # 404 chat_not_found — пользователь заблокировал бота. Прочие 4xx (токен, формат тела)
    # повтор тоже не исправит.
    return Delivery.FAILED, f"HTTP {status} {_error_code(response)}".rstrip()


def _json_field(response: httpx.Response, name: str) -> object:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get(name) if isinstance(body, dict) else None


def _error_code(response: httpx.Response) -> str:
    error = _json_field(response, "error")
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else ""


def _log(
    reminder: DueReminder, trace_id: str, delivery: Delivery, reason: str
) -> tuple[Delivery, str]:
    level = logging.INFO if delivery is Delivery.SENT else logging.WARNING
    logger.log(
        level,
        "notify %s attempt=%d trace=%s → %s %s",
        reminder.notification_id,
        reminder.attempt,
        trace_id,
        delivery.value,
        reason,
    )
    return delivery, reason
