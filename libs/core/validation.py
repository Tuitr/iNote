"""Ошибки pydantic → details для кода validation_error."""

from collections.abc import Sequence

from pydantic_core import ErrorDetails


def validation_error_details(errors: Sequence[ErrorDetails]) -> dict[str, list[str]]:
    """Имена полей без значений: значения — данные пользователя, им не место в ответе и логах.

    missing — поле не передано (так модель понимает, что надо переспросить, черновик D);
    invalid — передано, но не прошло проверку.
    """
    missing = sorted({_field_path(error["loc"]) for error in errors if error["type"] == "missing"})
    invalid = sorted({_field_path(error["loc"]) for error in errors if error["type"] != "missing"})
    details: dict[str, list[str]] = {}
    if missing:
        details["missing"] = missing
    if invalid:
        details["invalid"] = invalid
    return details


def _field_path(location: tuple[int | str, ...]) -> str:
    return ".".join(str(part) for part in location)
