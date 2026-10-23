import pytest
from pydantic import ValidationError

from libs.contracts.errors import (
    HTTP_STATUS_BY_CODE,
    ErrorCode,
    HttpErrorResponse,
    ToolErrorResponse,
)

PLAN_ERROR_EXAMPLE = {
    "error": {
        "code": "not_found",
        "message": "Запись не найдена",
        "details": {"id": "7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90"},
        "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
    }
}


def test_every_error_code_has_http_status() -> None:
    assert set(HTTP_STATUS_BY_CODE) == set(ErrorCode)


def test_plan_example_parses_and_round_trips() -> None:
    response = HttpErrorResponse.model_validate(PLAN_ERROR_EXAMPLE)

    assert response.error.code is ErrorCode.NOT_FOUND
    assert response.model_dump(mode="json") == PLAN_ERROR_EXAMPLE


@pytest.mark.parametrize(
    "trace_id",
    ["", "AB12F0C4D5E6F708192A3B4C5D6E7F80", "ab12f0c4", "zz12f0c4d5e6f708192a3b4c5d6e7f80"],
)
def test_invalid_trace_id_is_rejected(trace_id: str) -> None:
    payload = {"error": {**PLAN_ERROR_EXAMPLE["error"], "trace_id": trace_id}}

    with pytest.raises(ValidationError):
        HttpErrorResponse.model_validate(payload)


def test_unknown_error_code_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ToolErrorResponse.model_validate({"error": {"code": "oops", "message": "?"}})


def test_tool_error_has_no_trace_id_and_empty_details_by_default() -> None:
    response = ToolErrorResponse.model_validate(
        {"error": {"code": "validation_error", "message": "Не указано время встречи"}}
    )

    assert response.model_dump(mode="json") == {
        "error": {
            "code": "validation_error",
            "message": "Не указано время встречи",
            "details": {},
        }
    }
