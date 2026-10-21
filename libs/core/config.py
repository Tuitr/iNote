"""Настройки из переменных окружения, общие для сервисов роли D."""

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(RuntimeError):
    """Обязательная переменная окружения не задана."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    redis_url: str
    log_level: str


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"Переменная окружения {name} не задана")
    return value


def _log_level() -> str:
    value = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
    if value not in LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL={value!r}, допустимо: {', '.join(LOG_LEVELS)}")
    return value


def load_database_url() -> str:
    """Только адрес БД: миграциям не нужны Redis и остальные настройки."""
    return _require("DATABASE_URL")


def load_default_tz() -> str:
    """Пояс нового пользователя, пока он не выбрал свой (черновик D, UserSettings.tz)."""
    value = _require("DEFAULT_TZ")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ConfigError(f"DEFAULT_TZ={value!r}: неизвестный часовой пояс") from error
    return value


def load_service_token() -> str:
    """Токен, с которым другие сервисы ходят в mcp (КиП: Authorization: Bearer <SERVICE_TOKEN>)."""
    return _require("SERVICE_TOKEN")


def load_settings() -> Settings:
    return Settings(
        database_url=load_database_url(),
        redis_url=_require("REDIS_URL"),
        log_level=_log_level(),
    )


# HS256 с коротким ключом подбирается перебором: требуем не меньше 32 символов.
MIN_JWT_SECRET_LENGTH = 32


@dataclass(frozen=True)
class ApiAuthSettings:
    """Настройки авторизации мини-приложения (J5): нужны только сервису api."""

    bot_token: str
    jwt_secret: str
    dev_auth: bool


def load_api_auth_settings() -> ApiAuthSettings:
    jwt_secret = _require("API_JWT_SECRET")
    if len(jwt_secret) < MIN_JWT_SECRET_LENGTH:
        raise ConfigError(f"API_JWT_SECRET короче {MIN_JWT_SECRET_LENGTH} символов")
    return ApiAuthSettings(
        bot_token=_require("API_MAX_BOT_TOKEN"),
        jwt_secret=jwt_secret,
        dev_auth=_flag("API_DEV_AUTH"),
    )


def _flag(name: str) -> bool:
    value = os.environ.get(name, "false").strip().lower()
    if value not in ("true", "false"):
        raise ConfigError(f"{name}={value!r}, допустимо: true или false")
    return value == "true"
