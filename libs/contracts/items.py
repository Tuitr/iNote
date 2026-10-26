"""Запись пользователя (задача, напоминание, встреча): общая модель для J3, J4 и J5.

Поля и правила взяты из примера Item в КиП. Типы, обязательность и ограничения
в КиП не заданы и взяты из черновика D: до согласования с командой это v0.
"""

from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field


class ItemKind(StrEnum):
    TASK = "task"
    REMINDER = "reminder"
    EVENT = "event"


class ItemStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


class ItemBrief(BaseModel):
    """Короткая форма записи: для фокуса диалога, конфликтов и истории."""

    id: UUID
    kind: ItemKind
    title: str = Field(min_length=1, max_length=200)
    starts_at: AwareDatetime | None
    all_day: bool
    status: ItemStatus


class Item(ItemBrief):
    notes: str | None
    ends_at: AwareDatetime | None
    remind_at: AwareDatetime | None
    # Правило повторения RFC 5545 без префикса «RRULE:», например «FREQ=WEEKLY;BYDAY=MO».
    rrule: str | None
    participants: list[str]
    source_text: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    # Списки за период разворачивают серию в даты: 0 — сама запись (текущее вхождение),
    # 1, 2, … — следующие даты повтора с тем же id; действия с ними относятся ко всей серии.
    occurrence_index: int = Field(default=0, ge=0)
