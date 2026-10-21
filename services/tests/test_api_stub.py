import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services.api.app import app
from services.api.export_openapi import OPENAPI_PATH, render_openapi

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "j5"
AUTH = {"Authorization": "Bearer any-token"}


def _example(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_committed_openapi_is_up_to_date() -> None:
    # Поменяли контракт и не перевыгрузили OpenAPI — тест красный (КиП, снапшот-тесты).
    # Перевыгрузка: python -m services.api.export_openapi
    assert OPENAPI_PATH.read_text(encoding="utf-8") == render_openapi()


def test_auth_returns_example(client: TestClient) -> None:
    response = client.post("/v1/auth/max", json=_example("auth_max.request"))

    assert response.status_code == 200
    assert response.json() == _example("auth_max.response")


def test_auth_without_init_data_is_validation_error(client: TestClient) -> None:
    response = client.post("/v1/auth/max", json={})

    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"] == {"missing": ["body.init_data"]}
    assert len(error["trace_id"]) == 32


@pytest.mark.parametrize(
    ("method", "path"),
    [("get", "/v1/me"), ("get", "/v1/items?from=2026-10-19&to=2026-10-22")],
)
def test_protected_endpoints_require_token(client: TestClient, method: str, path: str) -> None:
    response = client.request(method, path)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_incoming_trace_id_is_echoed_in_error(client: TestClient) -> None:
    trace_id = "0123456789abcdef0123456789abcdef"

    response = client.get("/v1/me", headers={"X-Trace-Id": trace_id})

    assert response.json()["error"]["trace_id"] == trace_id


def test_me_and_patch_me(client: TestClient) -> None:
    assert client.get("/v1/me", headers=AUTH).json() == _example("me.response")

    response = client.patch("/v1/me", headers=AUTH, json=_example("me_patch.request"))

    assert response.json() == _example("me_patch.response")


def test_patch_me_rejects_unknown_timezone(client: TestClient) -> None:
    response = client.patch("/v1/me", headers=AUTH, json={"tz": "Mars/Olympus"})

    assert response.status_code == 400
    assert response.json()["error"]["details"] == {"invalid": ["body.tz"]}


@pytest.mark.parametrize(
    ("group", "example"), [("&group=day", "items_by_day.response"), ("", "items.response")]
)
def test_items_return_example_with_etag(client: TestClient, group: str, example: str) -> None:
    response = client.get(f"/v1/items?from=2026-10-19&to=2026-10-22{group}", headers=AUTH)

    assert response.status_code == 200
    assert response.json() == _example(example)
    assert response.headers["ETag"] == '"42"'


def test_items_with_matching_etag_return_304(client: TestClient) -> None:
    response = client.get(
        "/v1/items?from=2026-10-19&to=2026-10-22",
        headers={**AUTH, "If-None-Match": '"42"'},
    )

    assert response.status_code == 304
    assert response.content == b""


def test_items_reject_time_instead_of_date(client: TestClient) -> None:
    response = client.get("/v1/items?from=2026-10-19T00:00:00&to=2026-10-22", headers=AUTH)

    assert response.status_code == 400
    assert response.json()["error"]["details"] == {"invalid": ["query.from"]}


def test_search_returns_example(client: TestClient) -> None:
    response = client.get("/v1/items/search?q=Иван", headers=AUTH)

    assert response.json() == _example("items_search.response")


def test_item_by_id(client: TestClient) -> None:
    item = _example("item.response")

    assert client.get(f"/v1/items/{item['id']}", headers=AUTH).json() == item


def test_unknown_item_is_not_found(client: TestClient) -> None:
    response = client.get("/v1/items/00000000-0000-4000-8000-000000000000", headers=AUTH)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_malformed_item_id_is_validation_error(client: TestClient) -> None:
    response = client.get("/v1/items/7f3a9c2e", headers=AUTH)

    assert response.status_code == 400
