from services.api.app import create_app
from services.api.export_openapi import OPENAPI_PATH, render_openapi


def test_committed_openapi_is_up_to_date() -> None:
    # Поменяли контракт и не перевыгрузили OpenAPI — тест красный.
    # Перевыгрузка: python -m services.api.export_openapi
    assert OPENAPI_PATH.read_text(encoding="utf-8") == render_openapi()


def test_every_v1_success_response_has_an_example() -> None:
    # E собирает моки по примерам из OpenAPI (J5): ручка без примера останется без мока.
    spec = create_app().openapi()
    missing = [
        f"{method.upper()} {path}"
        for path, operations in spec["paths"].items()
        if path.startswith("/v1/")
        for method, operation in operations.items()
        if not {"example", "examples"}
        & operation["responses"]["200"]["content"]["application/json"].keys()
    ]

    assert missing == []


def test_mini_app_api_is_read_only() -> None:
    # Мини-приложение только показывает данные; войти — единственный POST (решение 2026-10-22).
    spec = create_app().openapi()
    operations = {
        f"{method.upper()} {path}"
        for path, methods in spec["paths"].items()
        if path.startswith("/v1/")
        for method in methods
    }

    assert {operation for operation in operations if not operation.startswith("GET ")} == {
        "POST /v1/auth/max"
    }
