"""Тела запросов и ответов REST API для мини-приложения (J5), v0.

Источник: КиП, раздел «J5 · MAX app → API». Поля, которых в КиП нет, отмечены
в docs/PROJECT_LOG.md как предложения D.
"""

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from libs.contracts.items import Item, ItemBrief
from libs.contracts.settings import IanaTimezone, UserSettings


class AuthMaxRequest(BaseModel):
    # Строка WebApp.initData как есть: пересборка ломает подпись (КиП, «Подпись initData»).
    init_data: str = Field(min_length=1)


class Me(BaseModel):
    name: str
    # tz лежит внутри settings, как в черновике D: одна модель UserSettings для J5, J8 и J2.
    settings: UserSettings


class AuthMaxResponse(BaseModel):
    token: str
    expires_at: AwareDatetime
    me: Me
    start_param: str | None


class MePatch(BaseModel):
    """Любое подмножество полей UserSettings; непереданные поля не меняются."""

    tz: IanaTimezone | None = None
    voice_replies: bool | None = None
    default_event_min: int | None = Field(default=None, ge=5, le=480)
    remind_before_min: int | None = Field(default=None, ge=0, le=1440)


class ItemsRange(BaseModel):
    model_config = ConfigDict(
        validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    from_: date = Field(alias="from")
    to: date
    tz: IanaTimezone


class ItemsDay(BaseModel):
    date: date
    label: str
    items: list[Item]


class ItemsByDayResponse(BaseModel):
    range: ItemsRange
    version: int
    days: list[ItemsDay]


class ItemsResponse(BaseModel):
    range: ItemsRange
    version: int
    items: list[Item]


class DialogTurnView(BaseModel):
    """Реплика в истории диалога. ЧЕРНОВИК D: формат истории утверждается на G3 (КиП)."""

    id: str
    role: Literal["user", "assistant"]
    text: str
    input_type: Literal["voice", "text"] | None
    created_at: AwareDatetime
    # У реплик ассистента — записи, о которых он ответил: ссылки на карточки.
    items: list[ItemBrief]


class DialogPage(BaseModel):
    """Страница истории, от новых реплик к старым. ЧЕРНОВИК D до G3."""

    turns: list[DialogTurnView]
    # Непрозрачная строка: передать в before, чтобы получить более старые; null — история кончилась.
    next_cursor: str | None
