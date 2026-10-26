"""Конверт ошибки и коды ошибок, общие для всех стыков."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "validation_error"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    # Запись не существует или принадлежит другому пользователю: ответ одинаковый,
    # чтобы по коду нельзя было узнать, есть ли чужая запись с таким id.
    NOT_FOUND = "not_found"
    STALE = "stale"
    PAST_TIME = "past_time"
    RATE_LIMITED = "rate_limited"
    INTERNAL = "internal"
    UPSTREAM_UNAVAILABLE = "upstream_unavailable"
    UPSTREAM_TIMEOUT = "upstream_timeout"


HTTP_STATUS_BY_CODE: dict[ErrorCode, int] = {
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


class ErrorInfo(BaseModel):
    """Тело ошибки без trace_id: в таком виде её возвращает MCP-инструмент."""

    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class HttpErrorInfo(ErrorInfo):
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class HttpErrorResponse(BaseModel):
    """Единый конверт ошибки для всех HTTP-ответов."""

    error: HttpErrorInfo


class ToolErrorResponse(BaseModel):
    """Содержимое ответа MCP-инструмента с isError: true."""

    error: ErrorInfo
