"""Фикстуры тестов HTTP-сервисов: api с настоящими PostgreSQL и Redis в Docker."""

from collections.abc import Iterator

import pytest
from docker.errors import DockerException
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL
from testcontainers.community.redis import RedisContainer

from services.api.app import create_app
from services.tests.init_data import BOT_TOKEN, JWT_SECRET


@pytest.fixture(scope="session")
def test_redis_url() -> Iterator[str]:
    """Redis 7 в Docker на время прогона — тот же образ, что в compose."""
    container = RedisContainer("redis:7-alpine")
    try:
        container.start()
    except DockerException as error:
        pytest.skip(f"Docker недоступен, тесты с Redis пропущены: {type(error).__name__}")
    try:
        host = container.get_container_host_ip()
        yield f"redis://{host}:{container.get_exposed_port(6379)}/0"
    finally:
        container.stop()


@pytest.fixture
def api_env(
    clean_database_url: URL, test_redis_url: str, monkeypatch: pytest.MonkeyPatch
) -> pytest.MonkeyPatch:
    monkeypatch.setenv("DATABASE_URL", clean_database_url.render_as_string(hide_password=False))
    monkeypatch.setenv("REDIS_URL", test_redis_url)
    monkeypatch.setenv("DEFAULT_TZ", "Europe/Moscow")
    monkeypatch.setenv("API_MAX_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("API_JWT_SECRET", JWT_SECRET)
    monkeypatch.delenv("API_DEV_AUTH", raising=False)
    return monkeypatch


@pytest.fixture
def api_client(api_env: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # with запускает lifespan: приложение стартует так же, как в контейнере.
    with TestClient(create_app()) as client:
        # Redis общий на прогон: версии данных прошлых тестов не должны мешать.
        client.portal.call(client.app.state.redis_client.flushdb)
        yield client
