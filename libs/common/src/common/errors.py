from collections.abc import Mapping
from enum import StrEnum
from typing import Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from common.trace import TRACE_ID_HEADER, currentTraceId
from common.wire import WireModel


# Примечание: коды ошибок из стандарта; строки на проводе в нижнем регистре
class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    STALE = "stale"
    PAST_TIME = "past_time"
    RATE_LIMITED = "rate_limited"
    INTERNAL = "internal"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_TIMEOUT = "upstream_timeout"


HTTP_STATUS_BY_CODE: Final[Mapping[str, int]] = {
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.STALE: 409,
    ErrorCode.PAST_TIME: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL: 500,
    ErrorCode.UPSTREAM_UNAVAILABLE: 503,
    ErrorCode.UPSTREAM_TIMEOUT: 504,
}

# Статусы Starlette (роут не найден, метод не тот) → код конверта
_CODE_BY_HTTP_STATUS: Final[Mapping[int, ErrorCode]] = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.NOT_FOUND,
    409: ErrorCode.STALE,
    422: ErrorCode.VALIDATION_ERROR,
    429: ErrorCode.RATE_LIMITED,
}


# Примечание: ошибка приложения; код может быть и чужим (no_speech у ASR) — тогда статус явно
class AppError(Exception):
    # Примечание: статус берётся из таблицы стандарта, если не передан явно
    def __init__(
        self,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
        *,
        httpStatus: int | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = str(code)
        self.message = message
        self.details: dict[str, object] = dict(details or {})
        self.httpStatus = httpStatus or HTTP_STATUS_BY_CODE.get(self.code, 500)


# Примечание: тело конверта ошибки
class ErrorBody(WireModel):
    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)
    traceId: str | None = None


# Примечание: конверт {"error": {...}} — единственный формат ошибок между сервисами
class ErrorEnvelope(WireModel):
    error: ErrorBody


# Примечание: HTTP-ответ с конвертом и X-Trace-Id; стектрейс наружу не уходит никогда
def errorResponse(error: AppError) -> JSONResponse:
    traceId = currentTraceId()
    body = ErrorEnvelope(
        error=ErrorBody(
            code=error.code, message=error.message, details=error.details, traceId=traceId
        )
    )
    headers = {TRACE_ID_HEADER: traceId} if traceId else None
    return JSONResponse(status_code=error.httpStatus, content=body.toWire(), headers=headers)


# Примечание: AppError из обработчика → конверт с его кодом
async def _handleAppError(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        raise exc
    return errorResponse(exc)


# Примечание: ошибка разбора запроса → validation_error 400 без входных значений (там текст)
async def _handleValidationError(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):
        raise exc
    fields = [
        {"loc": [str(part) for part in err.get("loc", ())], "type": str(err.get("type", ""))}
        for err in exc.errors()
    ]
    return errorResponse(
        AppError(ErrorCode.VALIDATION_ERROR, "запрос не прошёл проверку", {"fields": fields})
    )


# Примечание: HTTPException Starlette (404 роута, 405) → конверт
async def _handleHttpException(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    code = _CODE_BY_HTTP_STATUS.get(exc.status_code, ErrorCode.INTERNAL)
    return errorResponse(AppError(code, str(exc.detail), httpStatus=exc.status_code))


# Примечание: подключает обработчики; непойманные исключения ловит RequestContextMiddleware
def installErrorHandlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handleAppError)
    app.add_exception_handler(RequestValidationError, _handleValidationError)
    app.add_exception_handler(StarletteHTTPException, _handleHttpException)
