"""Проверка сервисного токена на mcp: Authorization: Bearer <SERVICE_TOKEN>.

Сделана чистым ASGI, а не BaseHTTPMiddleware: MCP отвечает потоком (Streamable HTTP),
а BaseHTTPMiddleware буферизует и ломает такие ответы.
"""

import hmac

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from libs.contracts.errors import ErrorCode
from services.http_errors import error_response

_OPEN_PREFIXES = ("/health",)


class ServiceTokenMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(_OPEN_PREFIXES):
            await self._app(scope, receive, send)
            return
        request = Request(scope)
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            response = error_response(request, ErrorCode.UNAUTHORIZED, "Нужен сервисный токен")
        elif not _same(token, request.app.state.service_token):
            response = error_response(request, ErrorCode.FORBIDDEN, "Неверный сервисный токен")
        else:
            await self._app(scope, receive, send)
            return
        await response(scope, receive, send)


def _same(token: str, expected: str) -> bool:
    # compare_digest сравнивает за одинаковое время: по скорости ответа нельзя подбирать токен.
    return hmac.compare_digest(token.encode(), expected.encode())
