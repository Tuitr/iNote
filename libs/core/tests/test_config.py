import pytest

from libs.core.config import ConfigError, load_settings


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
