from services.api.app import create_app
from services.api.export_openapi import OPENAPI_PATH, render_openapi


def test_committed_openapi_is_up_to_date() -> None:
    # Поменяли контракт и не перевыгрузили OpenAPI — тест красный (КиП, снапшот-тесты).
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
