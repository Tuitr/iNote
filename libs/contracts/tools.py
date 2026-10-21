"""Аргументы и ответы 8 MCP-инструментов (J3).

Источник: черновик D (раздел «J3: аргументы 8 инструментов») и КиП (раздел «J3»).
Никита решил, что до G1 черновик D считается контрактом для заглушек: это v0.
"""

import re
from datetime import date
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field

from libs.contracts.items import Item, ItemBrief, ItemKind
from libs.contracts.settings import IanaTimezone

_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")
# Полный UUID или его первые 8 символов: модели реже ошибаются в коротком id.
# Якоря ^ и $ обязательны: pattern в pydantic ищет совпадение в любом месте строки.
_ITEM_REF = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?$")


def _require_date_only(value: Any) -> Any:
    # Без этой проверки pydantic принял бы «2026-10-18T00:00:00» без смещения как дату,
    # а по КиП время без смещения — validation_error.
    if isinstance(value, str) and not _DATE_ONLY.fullmatch(value):
        raise ValueError("ожидается дата в формате YYYY-MM-DD")
    return value


DateOnly = Annotated[date, BeforeValidator(_require_date_only)]
# Дата без времени означает запись на весь день.
DateOrDateTime = AwareDatetime | DateOnly
ItemRef = Annotated[str, Field(pattern=_ITEM_REF.pattern)]
Title = Annotated[str, Field(min_length=1, max_length=200)]
TaskKind = Literal["task", "reminder"]


class Caller(BaseModel):
    """Служебный аргумент: заполняет агент, модель его не видит."""

    user_id: int
    tz: IanaTimezone
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    idempotency_key: str | None = None


class _ToolArgs(BaseModel):
    # «from» — ключевое слово Python, поэтому поле называется from_, а в JSON — «from».
    model_config = ConfigDict(
        validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    caller: Caller


class CreateTaskArgs(_ToolArgs):
    title: Title
    kind: TaskKind = "task"
    starts_at: DateOrDateTime | None = None
    remind_at: AwareDatetime | None = None
    notes: str | None = None
    rrule: str | None = None
    source_text: str | None = None


class CreateEventArgs(_ToolArgs):
    title: Title
    starts_at: DateOrDateTime
    ends_at: AwareDatetime | None = None
    participants: list[str] = Field(default_factory=list)
    remind_before_min: int | None = Field(default=None, ge=0)
    notes: str | None = None
    rrule: str | None = None
    source_text: str | None = None
    allow_conflict: bool = False


class ListItemsArgs(_ToolArgs):
    from_: DateOnly = Field(alias="from")
    to: DateOnly
    kind: list[ItemKind] | None = None
    status: Literal["open", "done", "all"] = "open"


class SearchItemsArgs(_ToolArgs):
    query: str = Field(min_length=1)
    kind: list[ItemKind] | None = None
    from_: DateOnly | None = Field(default=None, alias="from")
    to: DateOnly | None = None
    include_done: bool = True
    limit: int = Field(default=5, ge=1, le=10)


class UpdateItemArgs(_ToolArgs):
    """Переданные поля заменяются, null очищает поле, непереданные не меняются.

    Какие поля переданы, видно по model_fields_set: так null отличается от «не передано».
    """

    id: ItemRef
    title: Title | None = None
    kind: TaskKind | None = None
    starts_at: DateOrDateTime | None = None
    ends_at: AwareDatetime | None = None
    remind_at: AwareDatetime | None = None
    participants: list[str] | None = None
    remind_before_min: int | None = Field(default=None, ge=0)
    notes: str | None = None
    rrule: str | None = None
    source_text: str | None = None
    allow_conflict: bool = False


class CompleteItemArgs(_ToolArgs):
    id: ItemRef
    done: bool = True


class DeleteItemArgs(_ToolArgs):
    id: ItemRef
    restore: bool = False


class CheckConflictsArgs(_ToolArgs):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    exclude_id: ItemRef | None = None


class TimeSlot(BaseModel):
    starts_at: AwareDatetime
    ends_at: AwareDatetime


class DateRange(BaseModel):
    model_config = ConfigDict(
        validate_by_name=True, validate_by_alias=True, serialize_by_alias=True
    )

    from_: date = Field(alias="from")
    to: date


class MutationResult(BaseModel):
    """Ответ изменяющих инструментов. Конфликт — не ошибка, а status: conflict."""

    status: Literal["created", "updated", "completed", "deleted", "conflict"]
    item: Item | None
    conflicts: list[ItemBrief] = Field(default_factory=list)
    free_slots: list[TimeSlot] = Field(default_factory=list)


class ListItemsResult(BaseModel):
    items: list[Item] = Field(max_length=30)
    range: DateRange
    truncated: bool


class SearchHit(BaseModel):
    item: Item
    score: float


class SearchItemsResult(BaseModel):
    results: list[SearchHit]
    best_match_confident: bool


class CheckConflictsResult(BaseModel):
    conflicts: list[ItemBrief]
    free_slots: list[TimeSlot]


class ToolContract(BaseModel):
    arguments: type[_ToolArgs]
    result: type[BaseModel]


TOOLS: dict[str, ToolContract] = {
    "create_task": ToolContract(arguments=CreateTaskArgs, result=MutationResult),
    "create_event": ToolContract(arguments=CreateEventArgs, result=MutationResult),
    "list_items": ToolContract(arguments=ListItemsArgs, result=ListItemsResult),
    "search_items": ToolContract(arguments=SearchItemsArgs, result=SearchItemsResult),
    "update_item": ToolContract(arguments=UpdateItemArgs, result=MutationResult),
    "complete_item": ToolContract(arguments=CompleteItemArgs, result=MutationResult),
    "delete_item": ToolContract(arguments=DeleteItemArgs, result=MutationResult),
    "check_conflicts": ToolContract(arguments=CheckConflictsArgs, result=CheckConflictsResult),
}
