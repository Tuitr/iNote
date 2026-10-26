import hmac
import time
from collections.abc import Sequence
from typing import Final

from fastapi import FastAPI
from pydantic import SecretStr
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common.errors import AppError, ErrorCode, errorResponse
from common.logging import getLogger
from common.trace import TRACE_ID_HEADER, isValidTraceId, newTraceId, traceContext

INTERNAL_PREFIXES: Final = ("/internal/",)
# Пробы health дёргаются каждые несколько секунд — в INFO они только шум
QUIET_PATHS: Final = frozenset({"/health/live", "/health/ready"})
_BEARER: Final = "bearer "

_log = getLogger(__name__)


# Примечание: ASGI-middleware запроса: trace_id, лог с duration_ms, 500 internal на непойманное
class RequestContextMiddleware:
    # Примечание: чистый ASGI, без BaseHTTPMiddleware — не ломает contextvars и стриминг
    def __init__(self, app: ASGIApp, *, quietPaths: frozenset[str] = QUIET_PATHS) -> None:
        self._app = app
        self._quietPaths = quietPaths

    # Примечание: берёт X-Trace-Id из запроса (если каноничный) или создаёт новый
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get(TRACE_ID_HEADER)
        traceId = incoming if incoming and isValidTraceId(incoming) else newTraceId()
        with traceContext(traceId):
            await self._handle(scope, receive, send, traceId)

    # Примечание: прокидывает запрос, дописывает заголовок в ответ и пишет лог запроса
    async def _handle(self, scope: Scope, receive: Receive, send: Send, traceId: str) -> None:
        started = time.perf_counter()
        state = {"status": 500, "responseStarted": False}

        # Примечание: перехват начала ответа — статус для лога и X-Trace-Id в заголовки
        async def sendWithTrace(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["responseStarted"] = True
                MutableHeaders(scope=message)[TRACE_ID_HEADER] = traceId
            await send(message)

        try:
            await self._app(scope, receive, sendWithTrace)
        except Exception:
            _log.exception("unhandled_error", path=scope["path"])
            if state["responseStarted"]:
                raise
            response = errorResponse(AppError(ErrorCode.INTERNAL, "внутренняя ошибка"))
            await response(scope, receive, sendWithTrace)
        finally:
            durationMs = round((time.perf_counter() - started) * 1000)
            path = scope["path"]
            logMethod = _log.debug if path in self._quietPaths else _log.info
            # Только путь без query: в параметрах может оказаться пользовательский текст
            logMethod(
                "http_request",
                method=scope["method"],
                path=path,
                status=state["status"],
                duration_ms=durationMs,
            )


# Примечание: сервисный токен для внутренних путей: нет токена → 401, неверный → 403
class ServiceTokenMiddleware:
    # Примечание: защищаемые префиксы задаёт сервис (агент добавит /v1/)
    def __init__(
        self,
        app: ASGIApp,
        *,
        token: SecretStr,
        protectedPrefixes: Sequence[str] = INTERNAL_PREFIXES,
    ) -> None:
        self._app = app
        self._token = token.get_secret_value().encode()
        self._prefixes = tuple(protectedPrefixes)

    # Примечание: сравнение токена за постоянное время (hmac.compare_digest)
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._isProtected(scope["path"]):
            await self._app(scope, receive, send)
            return
        header = Headers(scope=scope).get("authorization", "")
        if not header.lower().startswith(_BEARER) or not header[len(_BEARER) :].strip():
            error = AppError(ErrorCode.UNAUTHORIZED, "нужен сервисный токен")
        elif not hmac.compare_digest(header[len(_BEARER) :].strip().encode(), self._token):
            error = AppError(ErrorCode.FORBIDDEN, "неверный сервисный токен")
        else:
            await self._app(scope, receive, send)
            return
        await errorResponse(error)(scope, receive, send)

    # Примечание: /internal и всё под ним; /internalx — не наш путь
    def _isProtected(self, path: str) -> bool:
        return any(path.startswith(p) or path == p.rstrip("/") for p in self._prefixes)


# Примечание: порядок важен — RequestContext снаружи, чтобы 401/403 тоже были с trace_id в логах
def installMiddleware(
    app: FastAPI,
    *,
    serviceToken: SecretStr,
    protectedPrefixes: Sequence[str] = INTERNAL_PREFIXES,
) -> None:
    app.add_middleware(
        ServiceTokenMiddleware, token=serviceToken, protectedPrefixes=protectedPrefixes
    )
    app.add_middleware(RequestContextMiddleware)
