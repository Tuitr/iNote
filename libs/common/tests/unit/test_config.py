from typing import ClassVar, Literal

import pytest
from pydantic import Field, ValidationError

from common.config import BaseServiceSettings

TOKEN = "t" * 32


# Примечание: окружение теста с обязательным токеном
@pytest.fixture
def baseEnv(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in BaseServiceSettings.model_fields:
        alias = BaseServiceSettings.model_fields[name].validation_alias
        assert isinstance(alias, str)
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    return monkeypatch


# Примечание: пример настроек сервиса с правильным префиксом
class DemoBotSettings(BaseServiceSettings):
    envPrefix: ClassVar[str] = "BOT_"
    maxMode: Literal["fake", "real"] = Field(default="fake", validation_alias="BOT_MAX_MODE")


# Примечание: имена переменных берутся из явных алиасов, атрибуты — camelCase
def test_readsEnvByAlias(baseEnv: pytest.MonkeyPatch) -> None:
    baseEnv.setenv("REDIS_URL", "redis://redis:6379/1")
    baseEnv.setenv("LOG_FORMAT", "json")
    baseEnv.setenv("BOT_MAX_MODE", "real")
    settings = DemoBotSettings()
    assert settings.redisUrl == "redis://redis:6379/1"
    assert settings.logFormat == "json"
    assert settings.maxMode == "real"
    assert settings.defaultTz == "Europe/Moscow"


# Примечание: без SERVICE_TOKEN сервис не стартует; токен не светится в repr
def test_serviceTokenRequiredAndSecret(baseEnv: pytest.MonkeyPatch) -> None:
    assert TOKEN not in repr(DemoBotSettings())
    baseEnv.delenv("SERVICE_TOKEN")
    with pytest.raises(ValidationError):
        DemoBotSettings()


# Примечание: короткий токен — ошибка конфигурации
def test_shortServiceTokenRejected(baseEnv: pytest.MonkeyPatch) -> None:
    baseEnv.setenv("SERVICE_TOKEN", "short")
    with pytest.raises(ValidationError):
        DemoBotSettings()


# Примечание: неверные значения перечислений и пояса ловятся на старте
@pytest.mark.parametrize(
    ("name", "value"),
    [("LOG_FORMAT", "xml"), ("LOG_LEVEL", "LOUD"), ("DEFAULT_TZ", "Mars/Olympus")],
)
def test_invalidValuesRejected(baseEnv: pytest.MonkeyPatch, name: str, value: str) -> None:
    baseEnv.setenv(name, value)
    with pytest.raises(ValidationError):
        DemoBotSettings()


# Примечание: имена переменных в нижнем регистре не подхватываются (case_sensitive)
def test_caseSensitive(baseEnv: pytest.MonkeyPatch) -> None:
    baseEnv.setenv("bot_max_mode", "real")
    assert DemoBotSettings().maxMode == "fake"


# Примечание: envNames — полный список переменных сервиса
def test_envNames() -> None:
    assert DemoBotSettings.envNames() == {
        "DATABASE_URL",
        "REDIS_URL",
        "SERVICE_TOKEN",
        "LOG_LEVEL",
        "LOG_FORMAT",
        "DEFAULT_TZ",
        "BOT_MAX_MODE",
    }


# Примечание: своё поле без префикса сервиса — ошибка при объявлении класса
def test_ownFieldWithoutPrefixRejected() -> None:
    with pytest.raises(TypeError, match="без префикса BOT_"):
        # Примечание: заведомо неверное объявление настроек
        class Bad(BaseServiceSettings):
            envPrefix: ClassVar[str] = "BOT_"
            timeoutS: int = Field(default=1, validation_alias="AGENT_TIMEOUT_S")


# Примечание: два поля на одну переменную — конфликт
def test_duplicateAliasRejected() -> None:
    with pytest.raises(TypeError, match="BOT_X"):
        # Примечание: заведомо неверное объявление настроек
        class Bad(BaseServiceSettings):
            envPrefix: ClassVar[str] = "BOT_"
            first: int = Field(default=1, validation_alias="BOT_X")
            second: int = Field(default=2, validation_alias="BOT_X")


# Примечание: поле без явного алиаса — ошибка (имя переменной не угадываем)
def test_missingAliasRejected() -> None:
    with pytest.raises(TypeError, match="нужен явный validation_alias"):
        # Примечание: заведомо неверное объявление настроек
        class Bad(BaseServiceSettings):
            envPrefix: ClassVar[str] = "BOT_"
            implicit: int = 1


# Примечание: префикс не из списка зон — ошибка
def test_unknownPrefixRejected() -> None:
    with pytest.raises(TypeError, match="envPrefix"):
        # Примечание: заведомо неверное объявление настроек
        class Bad(BaseServiceSettings):
            envPrefix: ClassVar[str] = "FOO_"


# Примечание: общее поле нельзя перевесить на другую переменную
def test_sharedFieldOverrideRejected() -> None:
    with pytest.raises(TypeError, match="общее поле"):
        # Примечание: заведомо неверное объявление настроек
        class Bad(BaseServiceSettings):
            envPrefix: ClassVar[str] = "BOT_"
            redisUrl: str = Field(default="x", validation_alias="BOT_REDIS_URL")
