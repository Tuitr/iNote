"""POST /internal/v1/notify (J4): Scheduler (D) → бот. Сервисный токен проверяет middleware
(/internal/*), X-Trace-Id приходит от Scheduler'а — цепочку начал он.

Коды ответа по J4: 200 delivered или duplicate → Scheduler ставит sent; 404 chat_not_found →
failed без повторов; 429 rate_limited и 503 upstream_unavailable → повтор с растущей паузой.
Параллельный повтор того же уведомления — 503 с Retry-After: Scheduler повторит позже, а к тому
времени первый запрос уже доставит и повтор получит duplicate.
"""

from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from bot.application.deliver_notification import DeliverNotification
from bot.domain.notifications import NotifyOutcome
from common.errors import AppError, ErrorCode, errorResponse
from contracts.notify import (
    CHAT_NOT_FOUND,
    CHAT_NOT_FOUND_STATUS,
    NOTIFY_PATH,
    NotifyDelivered,
    NotifyRequest,
)

# Через сколько секунд Scheduler'у стоит повторить параллельный запрос (отправка в MAX — секунды)
IN_PROGRESS_RETRY_AFTER_S: Final = 5


# Примечание: use case из app.state — собран в lifespan
def _deliver(request: Request) -> DeliverNotification:
    deliver: DeliverNotification = request.app.state.deliverNotification
    return deliver


# Примечание: роутер J4; подключается всегда — Scheduler шлёт и в режиме polling, и в webhook
def buildNotifyRouter() -> APIRouter:
    router = APIRouter(tags=["notify"])

    # Примечание: исход доставки → код ответа J4
    @router.post(NOTIFY_PATH, response_model=None)
    async def notify(request: Request, body: NotifyRequest) -> Response:
        result = await _deliver(request)(body)
        match result.outcome:
            case NotifyOutcome.DELIVERED:
                return JSONResponse(NotifyDelivered(messageId=result.messageId).toWire())
            case NotifyOutcome.DUPLICATE:
                delivered = NotifyDelivered(messageId=result.messageId, duplicate=True)
                return JSONResponse(delivered.toWire())
            case NotifyOutcome.CHAT_NOT_FOUND:
                return errorResponse(
                    AppError(
                        CHAT_NOT_FOUND,
                        "чат не найден или бот заблокирован",
                        httpStatus=CHAT_NOT_FOUND_STATUS,
                    )
                )
            case NotifyOutcome.RATE_LIMITED:
                return errorResponse(AppError(ErrorCode.RATE_LIMITED, "MAX ограничил отправку"))
            case NotifyOutcome.IN_PROGRESS:
                response = errorResponse(
                    AppError(
                        ErrorCode.UPSTREAM_UNAVAILABLE,
                        "уведомление уже отправляется",
                        {"reason": "in_progress"},
                    )
                )
                response.headers["Retry-After"] = str(IN_PROGRESS_RETRY_AFTER_S)
                return response
            case _:
                return errorResponse(AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "MAX недоступен"))

    return router
