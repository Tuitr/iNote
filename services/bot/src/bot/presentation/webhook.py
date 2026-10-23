"""POST /webhook/max: апдейт от MAX → сразу 200, обработка — в фоне через UpdateDispatcher.

MAX ждёт 200 до 30 с, иначе повторяет (до 10 раз) и через 8 ч без 200 отписывает бота.
Поэтому ответ не ждёт агента: разбор (без I/O) — здесь, обработка — в очереди пользователя.
Подлинность — по секрету подписки в X-Max-Bot-Api-Secret (dev.max.ru, POST /subscriptions).
Роутер подключается только при BOT_UPDATES_MODE=webhook — в polling путь отвечает 404.
"""

import hmac
import json
from typing import Final

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from bot.application.update_dispatcher import UpdateDispatcher
from bot.domain.updates import WEBHOOK_SECRET_HEADER, RawUpdateMapper
from common.errors import AppError, ErrorCode
from common.lifecycle import ShuttingDownError
from common.logging import getLogger

WEBHOOK_PATH: Final = "/webhook/max"
# Апдейт MAX — один JSON-объект; больше 1 МБ — не апдейт
MAX_BODY_BYTES: Final = 1024 * 1024

_log = getLogger(__name__)


# Примечание: диспетчер и секрет — из app.state (собраны в lifespan)
def _dispatcher(request: Request) -> UpdateDispatcher:
    dispatcher: UpdateDispatcher = request.app.state.dispatcher
    return dispatcher


# Примечание: webhook MAX; secret — BOT_WEBHOOK_SECRET (обязателен в этом режиме), mapUpdate —
# тот же маппер, что у long polling (infrastructure/max_mapper.py, передаётся из main)
def buildWebhookRouter(secret: str, mapUpdate: RawUpdateMapper) -> APIRouter:
    router = APIRouter(tags=["max"])
    expected = secret.encode()

    # Примечание: чужой секрет — 401 без чтения тела; больше 1 МБ — 400 (это не апдейт MAX);
    # битый или неподдержанный апдейт — 200 (иначе MAX будет повторять его 10 раз);
    # сервис останавливается — 503, MAX пришлёт повторно
    @router.post(WEBHOOK_PATH)
    async def webhook(request: Request) -> JSONResponse:
        given = request.headers.get(WEBHOOK_SECRET_HEADER, "").encode()
        if not hmac.compare_digest(given, expected):
            _log.warning("webhook_secret_mismatch", has_header=bool(given))
            raise AppError(ErrorCode.UNAUTHORIZED, "неверный секрет webhook")
        body = await _readLimited(request)
        if body is None:
            _log.warning("webhook_body_too_large", limit=MAX_BODY_BYTES)
            raise AppError(ErrorCode.VALIDATION_ERROR, "тело больше лимита")
        raw = _parse(body)
        update = mapUpdate(raw) if raw is not None else None
        if update is None:
            return JSONResponse({"ok": True})
        try:
            _dispatcher(request).submit(update)
        except ShuttingDownError:
            _log.warning("webhook_rejected_shutting_down", update_id=update.updateId)
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "бот останавливается") from None
        _log.info("webhook_accepted", update_id=update.updateId, kind=update.kind.value)
        return JSONResponse({"ok": True})

    return router


# Примечание: тело не больше MAX_BODY_BYTES — читаем потоком и бросаем, не дочитав огромное;
# None — больше лимита (по Content-Length или по факту)
async def _readLimited(request: Request) -> bytes | None:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


# Примечание: тело → JSON-объект; не JSON или не объект — None (с записью в лог)
def _parse(body: bytes) -> dict[str, object] | None:

    try:
        raw = json.loads(body)
    except ValueError:
        _log.warning("webhook_body_invalid", reason="not_json")
        return None
    if not isinstance(raw, dict):
        _log.warning("webhook_body_invalid", reason="not_object")
        return None
    return raw
