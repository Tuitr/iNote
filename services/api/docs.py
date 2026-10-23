"""Примеры ответов для OpenAPI: по ним E собирает моки (J5). Тела — из contracts/examples/j5."""

import json
from pathlib import Path
from typing import Any

from libs.contracts.errors import ErrorCode, HttpErrorResponse

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "j5"


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def json_example(name: str) -> dict[str, Any]:
    return {"content": {"application/json": {"example": example(name)}}}


def error_example(code: ErrorCode, message: str) -> dict[str, Any]:
    body = {
        "error": {
            "code": code,
            "message": message,
            "details": {},
            "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
        }
    }
    return {"model": HttpErrorResponse, "content": {"application/json": {"example": body}}}


UNAUTHORIZED = {401: error_example(ErrorCode.UNAUTHORIZED, "Нужна авторизация")}
VALIDATION = {400: error_example(ErrorCode.VALIDATION_ERROR, "Неверные поля запроса")}
