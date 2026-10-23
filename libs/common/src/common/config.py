import re
from typing import ClassVar, Final, Literal, override
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

type LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]
type LogFormat = Literal["console", "json"]

# Общие переменные (CLAUDE.md, правило 10); свои у сервиса — только с его префиксом
SHARED_ENV_NAMES: Final = frozenset(
    {"DATABASE_URL", "REDIS_URL", "SERVICE_TOKEN", "LOG_LEVEL", "LOG_FORMAT", "DEFAULT_TZ"}
)
KNOWN_PREFIXES: Final = ("BOT_", "AGENT_", "ASR_", "LLM_", "MCP_", "API_", "SCHED_")
_ENV_NAME_RE: Final = re.compile(r"^[A-Z][A-Z0-9_]*$")
MIN_SERVICE_TOKEN_LENGTH: Final = 16


# Примечание: общие настройки сервиса; наследник задаёт envPrefix и поля с явным validation_alias
class BaseServiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",
        frozen=True,
        case_sensitive=True,
        # .env читает compose; в коде источник один — окружение процесса
        env_file=None,
    )

    envPrefix: ClassVar[str] = ""

    databaseUrl: SecretStr | None = Field(
        default=None,
        validation_alias="DATABASE_URL",
        description="PostgreSQL (зона C не читает — для D)",
    )
    redisUrl: str = Field(
        default="redis://localhost:6379/0",
        validation_alias="REDIS_URL",
        description="Redis (ключи — `docs/redis-keys.md`)",
    )
    serviceToken: SecretStr = Field(
        validation_alias="SERVICE_TOKEN",
        description="сервисный токен внутренних вызовов (≥ 16 символов; на стенде с HTTPS ≥ 32)",
        min_length=MIN_SERVICE_TOKEN_LENGTH,
    )
    logLevel: LogLevel = Field(
        default="INFO",
        validation_alias="LOG_LEVEL",
        description="уровень логов: DEBUG, INFO, WARNING, ERROR",
    )
    logFormat: LogFormat = Field(
        default="console", validation_alias="LOG_FORMAT", description="`console` или `json`"
    )
    defaultTz: str = Field(
        default="Europe/Moscow",
        validation_alias="DEFAULT_TZ",
        description="пояс по умолчанию (IANA)",
    )

    # Примечание: пояс по умолчанию должен существовать в tzdata, иначе падаем на старте
    @field_validator("defaultTz")
    @classmethod
    def _checkTimezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"неизвестный часовой пояс: {value}"
            raise ValueError(msg) from exc
        return value

    # Примечание: проверка при объявлении класса — префиксы и алиасы не конфликтуют
    @classmethod
    @override
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        _validateEnvNames(cls)

    # Примечание: имена переменных окружения сервиса — для .env.example и тестов
    @classmethod
    def envNames(cls) -> frozenset[str]:
        return frozenset(_aliasOf(cls, name) for name in cls.model_fields)


# Примечание: имя переменной окружения поля; без явного alias — ошибка объявления
def _aliasOf(cls: type[BaseServiceSettings], fieldName: str) -> str:
    alias = cls.model_fields[fieldName].validation_alias
    if not isinstance(alias, str):
        msg = f"{cls.__name__}.{fieldName}: нужен явный validation_alias строкой"
        raise TypeError(msg)
    return alias


# Примечание: свои поля — с префиксом сервиса и UPPER_SNAKE; общие имена не переопределяются
def _validateEnvNames(cls: type[BaseServiceSettings]) -> None:
    prefix = cls.envPrefix
    if prefix not in KNOWN_PREFIXES:
        msg = f"{cls.__name__}.envPrefix={prefix!r}: ожидается один из {KNOWN_PREFIXES}"
        raise TypeError(msg)
    seen: dict[str, str] = {}
    for fieldName in cls.model_fields:
        alias = _aliasOf(cls, fieldName)
        if not _ENV_NAME_RE.fullmatch(alias):
            msg = f"{cls.__name__}.{fieldName}: {alias!r} — не UPPER_SNAKE"
            raise TypeError(msg)
        if alias in seen:
            msg = f"{cls.__name__}: {alias} у полей {seen[alias]} и {fieldName}"
            raise TypeError(msg)
        seen[alias] = fieldName
        isShared = fieldName in BaseServiceSettings.model_fields
        if isShared and alias not in SHARED_ENV_NAMES:
            msg = f"{cls.__name__}.{fieldName}: общее поле переопределено на {alias}"
            raise TypeError(msg)
        if not isShared and not alias.startswith(prefix):
            msg = f"{cls.__name__}.{fieldName}: {alias} без префикса {prefix}"
            raise TypeError(msg)
