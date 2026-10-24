import pytest
from pydantic import ValidationError

from brain.settings import LlmSettings

SERVICE_TOKEN = "s" * 32


# Примечание: окружение без переменных LLM_ — только общий токен агента
@pytest.fixture(autouse=True)
def cleanEnv(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in LlmSettings.envNames():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SERVICE_TOKEN", SERVICE_TOKEN)


# Примечание: по умолчанию — fake: без ключа, сети и денег; GigaChat-2-Max, 20 с, 4 шага
def test_defaultsAreFree() -> None:
    settings = LlmSettings()
    assert settings.provider == "fake"
    assert (settings.model, settings.timeoutS, settings.maxSteps) == ("GigaChat-2-Max", 20.0, 4)
    assert settings.gigachatCredentials is None


# Примечание: gigachat без ключа — ошибка на старте агента, а не на первом ходе
def test_gigachatNeedsCredentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gigachat")
    with pytest.raises(ValidationError, match="LLM_GIGACHAT_CREDENTIALS"):
        LlmSettings()
    monkeypatch.setenv("LLM_GIGACHAT_CREDENTIALS", "key")
    assert LlmSettings().gigachatCredentials is not None


# Примечание: таймаут вызова не больше 20 с — иначе два вызова не влезут в ход агента (40 с)
@pytest.mark.parametrize("value", ["0", "21"])
def test_timeoutWithinTurnBudget(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_S", value)
    with pytest.raises(ValidationError):
        LlmSettings()


# Примечание: свои переменные — только с префиксом LLM_ (стандарт)
def test_envNamesHavePrefix() -> None:
    own = LlmSettings.envNames() - {
        "DATABASE_URL",
        "REDIS_URL",
        "SERVICE_TOKEN",
        "LOG_LEVEL",
        "LOG_FORMAT",
        "DEFAULT_TZ",
    }
    assert own
    assert all(name.startswith("LLM_") for name in own)
