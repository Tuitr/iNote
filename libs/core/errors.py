"""Ошибки сервисов libs/core. Адаптер (MCP, HTTP) переводит их в конверт ошибки своего контракта."""

from typing import Any

from libs.contracts.errors import ErrorCode


class DomainError(Exception):
    code: ErrorCode

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(DomainError):
    code = ErrorCode.NOT_FOUND


class PastTimeError(DomainError):
    code = ErrorCode.PAST_TIME


class InvalidArgumentsError(DomainError):
    """Аргументы прошли схему, но нарушают правило; в details.missing / invalid — имена полей."""

    code = ErrorCode.VALIDATION_ERROR
