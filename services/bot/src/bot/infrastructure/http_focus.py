"""FocusNotifier по HTTP: POST /v1/focus агента — фоном в TaskRegistry (S3, шаг 3).

Уведомление уже доставлено — фокус только подсказка для «перенеси его…», поэтому ответ J4
его не ждёт, а сбой — warning без названия записи. Повторов нет: POST без ключа (стандарт).
"""

from typing import Final

from common.errors import AppError
from common.http import ServiceHttpClient
from common.lifecycle import ShuttingDownError, TaskRegistry
from common.logging import getLogger
from contracts.agent_side import ItemBrief
from contracts.focus import FOCUS_PATH, FocusRequest
from contracts.notify import NotifyRequest

TASK_PREFIX: Final = "focus"

_log = getLogger(__name__)


# Примечание: http — тот же клиент агента (BOT_AGENT_URL, сервисный токен)
class HttpFocusNotifier:
    # Примечание: tasks — реестр приложения: вызов переживает ответ Scheduler'у
    def __init__(self, http: ServiceHttpClient, tasks: TaskRegistry) -> None:
        self._http = http
        self._tasks = tasks

    # Примечание: ставит вызов в фон и сразу возвращается
    def itemDelivered(self, request: NotifyRequest) -> None:
        name = f"{TASK_PREFIX}:{request.notificationId}"
        try:
            _ = self._tasks.spawn(self._send(focusRequest(request)), name=name)
        except ShuttingDownError:
            _log.warning("focus_lost", notification_id=str(request.notificationId))

    # Примечание: любая ошибка — warning с кодом; contextvars (trace_id) — от запроса J4
    async def _send(self, request: FocusRequest) -> None:
        try:
            await self._http.post(FOCUS_PATH, json=request.toWire())
        except AppError as exc:
            _log.warning("focus_failed", error_code=exc.code)
            return
        _log.info("focus_sent", kind=request.item.kind)


# Примечание: запись уведомления → FocusRequest (поля ItemBrief из NotifyItem)
def focusRequest(request: NotifyRequest) -> FocusRequest:
    item = request.item
    return FocusRequest(
        maxUserId=request.user.maxUserId,
        maxChatId=request.user.maxChatId,
        item=ItemBrief(
            id=item.id,
            kind=item.kind,
            title=item.title,
            startsAt=item.startsAt,
            allDay=item.allDay,
            status=item.status,
            remindAt=item.remindAt,
        ),
    )
