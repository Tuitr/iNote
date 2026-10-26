"""Строка лога на каждый запрос с trace_id: по коду из ответа ошибку находят в логах."""

import logging
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from services.api.auth import issue_token
from services.tests.init_data import JWT_SECRET

pytestmark = pytest.mark.anyio

TRACE_ID = "ab12f0c4d5e6f708192a3b4c5d6e7f80"


def _auth() -> dict[str, str]:
    # Пользователь фикстуры user_id: max_user_id 123456789.
    token, _ = issue_token(123456789, JWT_SECRET, datetime.now(UTC))
    return {"Authorization": f"Bearer {token}"}


def _request_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage() for record in caplog.records if record.name == "services.http_common"
    ]


async def test_request_is_logged_with_incoming_trace_id(
    api_client: TestClient, user_id: int, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="services.http_common")

    api_client.get("/v1/me", headers={**_auth(), "X-Trace-Id": TRACE_ID})

    (line,) = _request_lines(caplog)
    assert line.startswith("GET /v1/me 200 ")
    assert line.endswith(f" ms trace_id={TRACE_ID}")


async def test_error_envelope_and_log_share_the_generated_trace_id(
    api_client: TestClient, user_id: int, caplog: pytest.LogCaptureFixture
) -> None:
    # Фронт пришёл без X-Trace-Id: trace_id создаёт API, и он один в ответе и в логе.
    caplog.set_level(logging.INFO, logger="services.http_common")

    response = api_client.get("/v1/items/7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90", headers=_auth())

    trace_id = response.json()["error"]["trace_id"]
    (line,) = _request_lines(caplog)
    assert response.status_code == 404
    assert line.startswith("GET /v1/items/7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90 404 ")
    assert line.endswith(f"trace_id={trace_id}")


def test_health_checks_are_not_logged(
    api_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="services.http_common")

    api_client.get("/health/live")

    assert _request_lines(caplog) == []
