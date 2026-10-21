"""J4 · scheduler (D) → bot (C): POST /internal/v1/notify. Владелец C, вызывает D. Черновик к G1.

Ответы: 200 NotifyDelivered (delivered или duplicate) → Scheduler ставит sent;
404 конверт ошибки chat_not_found → failed без повторов; 429, 5xx, таймаут → повтор, до 5 попыток.
Бот отсекает дубли по notification_id (va:v1:bot:notif:{notification_id}, 24 ч).
"""

import uuid
from typing import Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, field_validator

from common.wire import WireModel

NOTIFY_PATH: Final = "/internal/v1/notify"
CHAT_NOT_FOUND: Final = "chat_not_found"
CHAT_NOT_FOUND_STATUS: Final = 404
MAX_ATTEMPTS: Final = 5


# Примечание: получатель: куда доставить и в каком поясе показывать время
class NotifyUser(WireModel):
    maxUserId: int
    maxChatId: int
    tz: str

    # Примечание: пояс должен существовать — иначе бот не отформатирует время
    @field_validator("tz")
    @classmethod
    def _checkTz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"неизвестный часовой пояс: {value}"
            raise ValueError(msg) from exc
        return value


# Примечание: запись в уведомлении — поля Item, которые читает бот; полная модель Item у D
class NotifyItem(WireModel):
    id: uuid.UUID
    kind: Literal["task", "reminder", "event"]
    title: str = Field(min_length=1)
    notes: str | None = None
    startsAt: AwareDatetime | None = None
    endsAt: AwareDatetime | None = None
    allDay: bool = False
    remindAt: AwareDatetime | None = None
    status: Literal["open", "done", "cancelled"] = "open"


# Примечание: запрос Scheduler; attempt — номер попытки (1…5), notification_id — ключ дедупа
class NotifyRequest(WireModel):
    notificationId: uuid.UUID
    kind: Literal["task", "reminder", "event"]
    user: NotifyUser
    item: NotifyItem
    fireAt: AwareDatetime
    attempt: int = Field(ge=1, le=MAX_ATTEMPTS)


# Примечание: 200: доставлено впервые (message_id) или уже доставлялось (duplicate=true)
class NotifyDelivered(WireModel):
    delivered: Literal[True] = True
    messageId: str | None = None
    duplicate: bool = False
