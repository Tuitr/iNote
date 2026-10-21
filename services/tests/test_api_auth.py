"""Вход мини-приложения: проверка initData (без базы) и POST /v1/auth/max (с базой)."""

import secrets
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL

from services.api.app import create_app
from services.api.auth import AuthError, issue_token, read_init_data
from services.tests.init_data import BOT_TOKEN, signed_init_data

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
JWT_SECRET = secrets.token_urlsafe(32)


def _tampered(init_data: str) -> str:
    fields = dict(parse_qsl(init_data))
    fields["user"] = fields["user"].replace("123456789", "987654321")
    return urlencode(fields)


class TestInitData:
    def test_signed_init_data_gives_the_user(self) -> None:
        login = read_init_data(
            signed_init_data(123456789, NOW), BOT_TOKEN, NOW, check_signature=True
        )

        assert (login.max_user_id, login.name, login.start_param) == (
            123456789,
            "Степан Иванов",
            "today",
        )

    def test_changed_field_breaks_the_signature(self) -> None:
        with pytest.raises(AuthError, match="Подпись"):
            read_init_data(
                _tampered(signed_init_data(123456789, NOW)), BOT_TOKEN, NOW, check_signature=True
            )

    def test_other_bot_token_breaks_the_signature(self) -> None:
        init_data = signed_init_data(123456789, NOW, bot_token=BOT_TOKEN[::-1])

        with pytest.raises(AuthError, match="Подпись"):
            read_init_data(init_data, BOT_TOKEN, NOW, check_signature=True)

    def test_init_data_older_than_an_hour_is_rejected(self) -> None:
        old = signed_init_data(123456789, NOW - timedelta(hours=1, seconds=1))
        fresh = signed_init_data(123456789, NOW - timedelta(minutes=59))

        with pytest.raises(AuthError, match="устарел"):
            read_init_data(old, BOT_TOKEN, NOW, check_signature=True)
        login = read_init_data(fresh, BOT_TOKEN, NOW, check_signature=True)
        assert login.max_user_id == 123456789

    def test_dev_auth_skips_signature_and_age(self) -> None:
        unsigned = _tampered(signed_init_data(123456789, NOW - timedelta(days=1)))

        login = read_init_data(unsigned, BOT_TOKEN, NOW, check_signature=False)

        assert login.max_user_id == 987654321

    @pytest.mark.parametrize("init_data", ["", "user=", "auth_date=1&hash=x", "%%%"])
    def test_garbage_is_rejected(self, init_data: str) -> None:
        with pytest.raises(AuthError):
            read_init_data(init_data, BOT_TOKEN, NOW, check_signature=False)

    def test_token_lives_one_hour_and_carries_max_user_id(self) -> None:
        token, expires_at = issue_token(123456789, JWT_SECRET, NOW)

        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False})
        assert payload["sub"] == "123456789"
        assert expires_at == NOW + timedelta(hours=1)
        assert payload["exp"] == int(expires_at.timestamp())


@pytest.fixture
def api_env(clean_database_url: URL, monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("DATABASE_URL", clean_database_url.render_as_string(hide_password=False))
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setenv("DEFAULT_TZ", "Europe/Moscow")
    monkeypatch.setenv("API_MAX_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("API_JWT_SECRET", JWT_SECRET)
    monkeypatch.delenv("API_DEV_AUTH", raising=False)
    return monkeypatch


@pytest.fixture
def client(api_env: pytest.MonkeyPatch) -> Iterator[TestClient]:
    with TestClient(create_app()) as test_client:
        yield test_client


def _now() -> datetime:
    return datetime.now(UTC)


def test_valid_init_data_gives_token_me_and_start_param(client: TestClient) -> None:
    response = client.post("/v1/auth/max", json={"init_data": signed_init_data(123456789, _now())})

    assert response.status_code == 200
    body = response.json()
    payload = jwt.decode(body["token"], JWT_SECRET, algorithms=["HS256"])
    assert payload["sub"] == "123456789"
    assert body["expires_at"].endswith("+03:00")
    assert body["me"] == {
        "name": "Степан Иванов",
        "settings": {
            "tz": "Europe/Moscow",
            "voice_replies": False,
            "default_event_min": 60,
            "remind_before_min": 15,
        },
    }
    assert body["start_param"] == "today"


@pytest.mark.parametrize(
    "init_data",
    [
        _tampered(signed_init_data(123456789, datetime.now(UTC))),
        signed_init_data(123456789, datetime.now(UTC) - timedelta(hours=2)),
    ],
    ids=["подделанная подпись", "старый auth_date"],
)
def test_bad_init_data_is_unauthorized(client: TestClient, init_data: str) -> None:
    response = client.post("/v1/auth/max", json={"init_data": init_data})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_without_init_data_is_validation_error(client: TestClient) -> None:
    response = client.post("/v1/auth/max", json={})

    assert response.status_code == 400
    assert response.json()["error"]["details"] == {"missing": ["body.init_data"]}


def test_dev_auth_is_off_by_default_and_works_when_enabled(api_env: pytest.MonkeyPatch) -> None:
    fake = _tampered(signed_init_data(123456789, _now()))

    with TestClient(create_app()) as default_client:
        rejected = default_client.post("/v1/auth/max", json={"init_data": fake})
    api_env.setenv("API_DEV_AUTH", "true")
    with TestClient(create_app()) as dev_client:
        accepted = dev_client.post("/v1/auth/max", json={"init_data": fake})

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    token = accepted.json()["token"]
    assert jwt.decode(token, JWT_SECRET, algorithms=["HS256"])["sub"] == "987654321"
