"""Общая HTTP-часть сервисов mcp и api: подключения, health и лог запросов с trace_id.

Временная, до libs/common роли C (там логи structlog и middleware X-Trace-Id по КиП).
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response, status
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from libs.contracts.health import ReadinessResponse
from libs.core.config import load_settings
from libs.core.health import check_readiness
from libs.core.log_setup import configure_logging
from libs.core.resources import create_db_engine, create_redis_client
from services.http_errors import incoming_trace_id

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    configure_logging(settings.log_level)
    app.state.db_engine = create_db_engine(settings.database_url)
    app.state.redis_client = create_redis_client(settings.redis_url)
    try:
        yield
    finally:
        await app.state.redis_client.aclose()
        await app.state.db_engine.dispose()


health_router = APIRouter(prefix="/health", tags=["health"])


@health_router.get("/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/ready")
async def health_ready(request: Request, response: Response) -> ReadinessResponse:
    readiness = await check_readiness(request.app.state.db_engine, request.app.state.redis_client)
    if readiness.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return readiness


class RequestLogMiddleware:
    """Строка лога на каждый запрос: метод, путь, статус, время и trace_id.

    Чистый ASGI, а не BaseHTTPMiddleware: MCP отвечает потоком, а BaseHTTPMiddleware его ломает.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # /health дёргает healthcheck каждые 10 секунд: в логе это только шум.
        if scope["type"] != "http" or scope["path"].startswith("/health"):
            await self._app(scope, receive, send)
            return
        trace_id = incoming_trace_id(Headers(scope=scope).get("X-Trace-Id", ""))
        # Обработчики ошибок берут trace_id отсюда, поэтому в ответе и в логе он один.
        scope.setdefault("state", {})["trace_id"] = trace_id
        status_code = 500
        started = time.perf_counter()

        async def remember_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self._app(scope, receive, remember_status)
        finally:
            logger.info(
                "%s %s %s %.0f ms trace_id=%s",
                scope["method"],
                scope["path"],
                status_code,
                (time.perf_counter() - started) * 1000,
                trace_id,
            )
