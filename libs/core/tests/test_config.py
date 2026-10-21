from collections.abc import Callable

import pytest

from libs.core.config import (
    ConfigError,
    load_api_auth_settings,
    load_default_tz,
    load_service_token,
    load_settings,
)


@pytest.fixture
def required_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:password@localhost:5432/db")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    return monkeypatch


def test_defaults_when_only_required_vars_are_set(required_env: pytest.MonkeyPatch) -> None:
    settings = load_settings()

    assert settings.log_level == "INFO"


@pytest.mark.parametrize("name", ["DATABASE_URL", "REDIS_URL"])
def test_missing_required_var_raises(required_env: pytest.MonkeyPatch, name: str) -> None:
    required_env.setenv(name, "  ")

    with pytest.raises(ConfigError, match=name):
        load_settings()


def test_log_level_is_normalized(required_env: pytest.MonkeyPatch) -> None:
    required_env.setenv("LOG_LEVEL", " debug ")

    assert load_settings().log_level == "DEBUG"


def test_unknown_log_level_raises(required_env: pytest.MonkeyPatch) -> None:
    required_env.setenv("LOG_LEVEL", "LOUD")

    with pytest.raises(ConfigError, match="LOG_LEVEL"):
        load_settings()


def test_default_tz_is_checked_against_tz_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_TZ", "Europe/Moscow")
    assert load_default_tz() == "Europe/Moscow"

    monkeypatch.setenv("DEFAULT_TZ", "Mars/Olympus")
    with pytest.raises(ConfigError, match="DEFAULT_TZ"):
        load_default_tz()


@pytest.mark.parametrize("load", [load_default_tz, load_service_token])
def test_missing_service_var_raises(
    monkeypatch: pytest.MonkeyPatch, load: Callable[[], str]
) -> None:
    monkeypatch.delenv("DEFAULT_TZ", raising=False)
    monkeypatch.delenv("SERVICE_TOKEN", raising=False)

    with pytest.raises(ConfigError, match="не задана"):
        load()


def test_api_auth_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_MAX_BOT_TOKEN", "123:token")
    monkeypatch.setenv("API_JWT_SECRET", "x" * 32)
    monkeypatch.delenv("API_DEV_AUTH", raising=False)
    assert load_api_auth_settings().dev_auth is False

    monkeypatch.setenv("API_DEV_AUTH", "TRUE")
    assert load_api_auth_settings().dev_auth is True

    monkeypatch.setenv("API_DEV_AUTH", "yes")
    with pytest.raises(ConfigError, match="API_DEV_AUTH"):
        load_api_auth_settings()

    monkeypatch.setenv("API_DEV_AUTH", "false")
    monkeypatch.setenv("API_JWT_SECRET", "short")
    with pytest.raises(ConfigError, match="API_JWT_SECRET"):
        load_api_auth_settings()
