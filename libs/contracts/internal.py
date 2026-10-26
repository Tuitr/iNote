"""Служебный REST на MCP-контейнере (J8): запросы агента C к данным D."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from libs.contracts.settings import IanaTimezone, UserSettings


class ResolveUserRequest(BaseModel):
    max_user_id: int
    max_chat_id: int | None = None
    name: str | None = None


class ResolveUserResponse(BaseModel):
    user_id: int
    # tz дублирует settings.tz: по J8 он на верхнем уровне, а UserSettings содержит его внутри.
    tz: IanaTimezone
    settings: UserSettings
    created: bool


class UserSettingsPatch(BaseModel):
    """Тело PATCH /internal/v1/users/{user_id}/settings: любое подмножество полей UserSettings.

    Непереданные поля и null не меняются. Ответ — UserSettings целиком (J8).
    Мини-приложение только читает, поэтому настройки меняются через бота.
    """

    tz: IanaTimezone | None = None
    voice_replies: bool | None = None
    default_event_min: int | None = Field(default=None, ge=5, le=480)
    remind_before_min: int | None = Field(default=None, ge=0, le=1440)


class DialogTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str
    # Голосом или текстом сказал пользователь; у реплик ассистента не заполняется.
    input_type: Literal["voice", "text"] | None = None
    # Записи, о которых ответил ассистент: ссылки на карточки в истории (J5).
    item_ids: list[UUID] = Field(default_factory=list)


class DialogTurnsRequest(BaseModel):
    """Один ход диалога. Повтор с тем же trace_id не создаёт дубль (J8)."""

    user_id: int
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    turns: list[DialogTurn] = Field(min_length=1)
    latency_ms: int | None = Field(default=None, ge=0)
