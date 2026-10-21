import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from libs.contracts.health import ReadinessResponse
from services import http_common
from services.api.app import app as api_app
from services.mcp.app import create_app as create_mcp_app


@pytest.fixture(params=[api_app, create_mcp_app()], ids=["api", "mcp"])
def app(request: pytest.FixtureRequest) -> FastAPI:
    service_app: FastAPI = request.param
    # TestClient без with не запускает lifespan, поэтому реальные подключения не создаются,
    # а check_readiness ниже подменяется и их не использует.
    service_app.state.db_engine = None
    service_app.state.redis_client = None
    return service_app


def _stub_readiness(monkeypatch: pytest.MonkeyPatch, readiness: ReadinessResponse) -> None:
    async def fake_check_readiness(engine: object, redis_client: object) -> ReadinessResponse:
        return readiness

    monkeypatch.setattr(http_common, "check_readiness", fake_check_readiness)


def test_live_returns_ok(app: FastAPI) -> None:
    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_200_when_all_checks_pass(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_readiness(
        monkeypatch,
        ReadinessResponse(status="ok", checks={"postgres": "ok", "redis": "ok"}),
    )

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}


def test_ready_returns_503_when_a_check_fails(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_readiness(
        monkeypatch,
        ReadinessResponse(status="degraded", checks={"postgres": "ok", "redis": "fail"}),
    )

    response = TestClient(app).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "checks": {"postgres": "ok", "redis": "fail"},
    }
