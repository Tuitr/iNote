"""Настройки из переменных окружения, общие для сервисов роли D."""

import os
from dataclasses import dataclass

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


def load_settings() -> Settings:
    return Settings(
        database_url=_require("DATABASE_URL"),
        redis_url=_require("REDIS_URL"),
        log_level=_log_level(),
    )
