import secrets
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL

from services.mcp.app import create_app

SERVICE_TOKEN = secrets.token_urlsafe()
AUTH = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
TRACE_ID = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
RESOLVE_BODY = {"max_user_id": 123456789, "max_chat_id": 987654321, "name": "Степан"}


@pytest.fixture
def client(clean_database_url: URL, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", clean_database_url.render_as_string(hide_password=False))
    # Redis этим маршрутам не нужен, а клиент к нему подключается только при первом запросе.
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("DEFAULT_TZ", "Europe/Moscow")
    monkeypatch.setenv("SERVICE_TOKEN", SERVICE_TOKEN)
    # with запускает lifespan: приложение стартует так же, как в контейнере.
    with TestClient(create_app()) as test_client:
        yield test_client


def test_resolve_creates_user_then_finds_it(client: TestClient) -> None:
    first = client.post("/internal/v1/users/resolve", json=RESOLVE_BODY, headers=AUTH)
    second = client.post("/internal/v1/users/resolve", json=RESOLVE_BODY, headers=AUTH)

    assert first.status_code == 200
    assert first.json() == {
        "user_id": 1,
        "tz": "Europe/Moscow",
        "settings": {
            "tz": "Europe/Moscow",
            "voice_replies": False,
            "default_event_min": 60,
            "remind_before_min": 15,
        },
        "created": True,
    }
    assert second.status_code == 200
    assert second.json()["user_id"] == 1
    assert second.json()["created"] is False


def test_resolve_without_max_user_id_is_validation_error(client: TestClient) -> None:
    response = client.post("/internal/v1/users/resolve", json={"name": "Степан"}, headers=AUTH)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["details"] == {"missing": ["body.max_user_id"]}


@pytest.mark.parametrize("path", ["/internal/v1/users/resolve", "/mcp"])
def test_request_without_token_is_unauthorized(client: TestClient, path: str) -> None:
    response = client.post(path, json=RESOLVE_BODY, headers={"X-Trace-Id": TRACE_ID})

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "unauthorized",
            "message": "Нужен сервисный токен",
            "details": {},
            "trace_id": TRACE_ID,
        }
    }


@pytest.mark.parametrize("path", ["/internal/v1/users/resolve", "/mcp"])
def test_request_with_wrong_token_is_forbidden(client: TestClient, path: str) -> None:
    response = client.post(path, json=RESOLVE_BODY, headers={"Authorization": "Bearer wrong"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_health_needs_no_token(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200


def _dialog_body(user_id: int) -> dict:
    return {
        "user_id": user_id,
        "trace_id": TRACE_ID,
        "turns": [
            {"role": "user", "text": "Напомни завтра в 10 позвонить Ивану", "input_type": "voice"},
            {"role": "assistant", "text": "Напомню завтра в 10:00."},
        ],
        "latency_ms": 2930,
    }


def test_dialog_turns_are_accepted_and_repeat_is_harmless(client: TestClient) -> None:
    user_id = client.post("/internal/v1/users/resolve", json=RESOLVE_BODY, headers=AUTH).json()[
        "user_id"
    ]

    first = client.post("/internal/v1/dialog-turns", json=_dialog_body(user_id), headers=AUTH)
    repeat = client.post("/internal/v1/dialog-turns", json=_dialog_body(user_id), headers=AUTH)

    assert (first.status_code, first.content) == (202, b"")
    assert repeat.status_code == 202


def test_dialog_turns_for_unknown_user_is_not_found(client: TestClient) -> None:
    response = client.post(
        "/internal/v1/dialog-turns",
        json=_dialog_body(999),
        headers={**AUTH, "X-Trace-Id": TRACE_ID},
    )

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "not_found",
            "message": "Пользователь не найден",
            "details": {},
            "trace_id": TRACE_ID,
        }
    }


def test_dialog_turns_without_turns_is_validation_error(client: TestClient) -> None:
    body = {**_dialog_body(1), "turns": []}

    response = client.post("/internal/v1/dialog-turns", json=body, headers=AUTH)

    assert response.status_code == 400
    assert response.json()["error"]["details"] == {"invalid": ["body.turns"]}


def test_settings_change_through_bot(client: TestClient) -> None:
    # «У меня теперь Екатеринбург, и отвечай голосом»
    user_id = client.post("/internal/v1/users/resolve", json=RESOLVE_BODY, headers=AUTH).json()[
        "user_id"
    ]

    changed = client.patch(
        f"/internal/v1/users/{user_id}/settings",
        json={"tz": "Asia/Yekaterinburg", "voice_replies": True},
        headers=AUTH,
    )
    again = client.post("/internal/v1/users/resolve", json=RESOLVE_BODY, headers=AUTH)

    assert changed.status_code == 200
    assert changed.json() == {
        "tz": "Asia/Yekaterinburg",
        "voice_replies": True,
        "default_event_min": 60,
        "remind_before_min": 15,
    }
    assert again.json()["tz"] == "Asia/Yekaterinburg"


def test_settings_validation_and_unknown_user(client: TestClient) -> None:
    user_id = client.post("/internal/v1/users/resolve", json=RESOLVE_BODY, headers=AUTH).json()[
        "user_id"
    ]

    bad = client.patch(
        f"/internal/v1/users/{user_id}/settings", json={"tz": "Mars/Olympus"}, headers=AUTH
    )
    unknown = client.patch(
        f"/internal/v1/users/{user_id + 100}/settings", json={"voice_replies": True}, headers=AUTH
    )

    assert bad.status_code == 400
    assert bad.json()["error"]["details"] == {"invalid": ["body.tz"]}
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"


def test_settings_need_service_token(client: TestClient) -> None:
    response = client.patch("/internal/v1/users/1/settings", json={"voice_replies": True})

    assert response.status_code == 401
