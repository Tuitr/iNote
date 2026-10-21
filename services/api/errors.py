"""Перевод ошибок API в общий конверт ошибки (КиП, раздел «Ошибки»)."""

import re
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from libs.contracts.errors import HTTP_STATUS_BY_CODE, ErrorCode, HttpErrorInfo, HttpErrorResponse
from libs.core.validation import validation_error_details

_TRACE_ID = re.compile(r"^[0-9a-f]{32}$")


class ApiError(Exception):
    def __init__(
        self, code: ErrorCode, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _trace_id(request: Request) -> str:
    # По КиП API начинает цепочку для запросов фронта, поэтому создаёт trace_id сам,
    # если корректного X-Trace-Id в запросе нет.
    incoming = request.headers.get("X-Trace-Id", "")
    return incoming if _TRACE_ID.fullmatch(incoming) else uuid.uuid4().hex


def _error_response(
    request: Request, code: ErrorCode, message: str, details: dict[str, Any]
) -> JSONResponse:
    body = HttpErrorResponse(
        error=HttpErrorInfo(
            code=code, message=message, details=details, trace_id=_trace_id(request)
        )
    )
    return JSONResponse(status_code=HTTP_STATUS_BY_CODE[code], content=body.model_dump(mode="json"))


async def _handle_api_error(request: Request, error: ApiError) -> JSONResponse:
    return _error_response(request, error.code, error.message, error.details)


async def _handle_validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
    details = validation_error_details(error.errors())
    return _error_response(request, ErrorCode.VALIDATION_ERROR, "Неверные поля запроса", details)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
