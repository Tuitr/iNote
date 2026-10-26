"""Перевод ошибок HTTP-сервисов D (api, mcp) в общий конверт ошибки (КиП, раздел «Ошибки»)."""

import logging
import re
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from libs.contracts.errors import HTTP_STATUS_BY_CODE, ErrorCode, HttpErrorInfo, HttpErrorResponse
from libs.core.errors import DomainError
from libs.core.validation import validation_error_details

_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")

logger = logging.getLogger(__name__)


class HttpError(Exception):
    def __init__(
        self, code: ErrorCode, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def incoming_trace_id(header: str) -> str:
    """trace_id из X-Trace-Id или новый.

    По КиП цепочку начинает тот, к кому запрос пришёл первым (API — для запросов фронта),
    поэтому без корректного X-Trace-Id trace_id создаётся здесь.
    """
    return header if _TRACE_ID.fullmatch(header) else uuid.uuid4().hex


def _trace_id(request: Request) -> str:
    # Лог запроса уже выбрал trace_id (RequestLogMiddleware): в ответе должен быть тот же.
    chosen: str | None = getattr(request.state, "trace_id", None)
    return chosen or incoming_trace_id(request.headers.get("X-Trace-Id", ""))


def error_response(
    request: Request, code: ErrorCode, message: str, details: dict[str, Any] | None = None
) -> JSONResponse:
    return _envelope(code, message, details or {}, _trace_id(request))


def _envelope(
    code: ErrorCode, message: str, details: dict[str, Any], trace_id: str
) -> JSONResponse:
    body = HttpErrorResponse(
        error=HttpErrorInfo(code=code, message=message, details=details, trace_id=trace_id)
    )
    return JSONResponse(status_code=HTTP_STATUS_BY_CODE[code], content=body.model_dump(mode="json"))


async def _handle_http_error(request: Request, error: HttpError) -> JSONResponse:
    return error_response(request, error.code, error.message, error.details)


async def _handle_domain_error(request: Request, error: DomainError) -> JSONResponse:
    return error_response(request, error.code, error.message, error.details)


async def _handle_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    details = validation_error_details(error.errors())
    return error_response(request, ErrorCode.VALIDATION_ERROR, "Неверные поля запроса", details)


async def _handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    # Ошибка в нашем коде или упала база: клиенту — конверт internal без подробностей
    # (в них может быть SQL), в лог — trace_id и traceback, чтобы найти по коду из ответа.
    trace_id = _trace_id(request)
    logger.error(
        "%s %s упал, trace_id=%s", request.method, request.url.path, trace_id, exc_info=error
    )
    return _envelope(ErrorCode.INTERNAL, "Внутренняя ошибка", {}, trace_id)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HttpError, _handle_http_error)
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
