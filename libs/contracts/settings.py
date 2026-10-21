"""Настройки пользователя: одна модель для J8, /v1/me (J5) и TurnInput.settings.

Поля, типы и значения по умолчанию взяты из черновика D: до согласования с командой это v0.
"""

from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, Field


def _require_iana_timezone(name: str) -> str:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError(f"Неизвестный часовой пояс: {name!r}") from error
    return name


IanaTimezone = Annotated[str, AfterValidator(_require_iana_timezone)]


class UserSettings(BaseModel):
    # Значения по умолчанию для tz нет: по черновику D он берётся из DEFAULT_TZ окружения,
    # а модель контракта окружение не читает. Подставляет его бэкенд при создании пользователя.
    tz: IanaTimezone
    voice_replies: bool = False
    default_event_min: int = Field(default=60, ge=5, le=480)
    remind_before_min: int = Field(default=15, ge=0, le=1440)
