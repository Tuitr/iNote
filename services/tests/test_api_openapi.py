from services.api.export_openapi import OPENAPI_PATH, render_openapi


def test_committed_openapi_is_up_to_date() -> None:
    # Поменяли контракт и не перевыгрузили OpenAPI — тест красный (КиП, снапшот-тесты).
    # Перевыгрузка: python -m services.api.export_openapi
    assert OPENAPI_PATH.read_text(encoding="utf-8") == render_openapi()
