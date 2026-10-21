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
