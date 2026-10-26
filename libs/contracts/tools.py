"""Аргументы и ответы 8 MCP-инструментов (J3)."""

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
    # а время без смещения по контракту — validation_error.
    if isinstance(value, str) and not _DATE_ONLY.fullmatch(value):
        raise ValueError("ожидается дата в формате YYYY-MM-DD")
    return value


DateOnly = Annotated[date, BeforeValidator(_require_date_only)]
# Дата без времени означает запись на весь день.
DateOrDateTime = AwareDatetime | DateOnly
ItemRef = Annotated[str, Field(pattern=_ITEM_REF.pattern)]
Title = Annotated[str, Field(min_length=1, max_length=200)]
# Синонимы и близкие слова от LLM: по ним запись потом находится по смыслу (поиск без эмбеддера).
SearchHints = Annotated[str, Field(max_length=500)]
TaskKind = Literal["task", "reminder"]
# Описания полей уходят модели в схеме инструмента (B): когда и в каком формате передавать поле
_RRULE_HINT = (
    "Повторение RFC 5545 без «RRULE:», например FREQ=WEEKLY;BYDAY=MO; только если просят повторять"
)
_SEARCH_HINTS_HINT = (
    "Синонимы и близкие слова через пробел, по ним запись найдут потом: звонок позвонить созвон"
)


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
    title: Title = Field(description="Короткое название по-русски, без «напомни», дат и времени")
    kind: TaskKind = Field(
        default="task", description="task — задача; reminder — напоминание в момент remind_at"
    )
    starts_at: DateOrDateTime | None = Field(
        default=None,
        description=(
            "Срок задачи: дата YYYY-MM-DD (весь день) или время ISO 8601 "
            "со смещением пояса пользователя"
        ),
    )
    remind_at: AwareDatetime | None = Field(
        default=None,
        description=(
            "Когда напомнить, ISO 8601 со смещением пояса пользователя; обязательно для reminder"
        ),
    )
    notes: str | None = Field(default=None, description="Подробности, если пользователь их назвал")
    rrule: str | None = Field(
        default=None,
        description=_RRULE_HINT,
    )
    source_text: str | None = Field(default=None, description="Исходная фраза пользователя")
    search_hints: SearchHints | None = Field(
        default=None,
        description=_SEARCH_HINTS_HINT,
    )


class CreateEventArgs(_ToolArgs):
    title: Title = Field(description="Короткое название по-русски, без «напомни», дат и времени")
    starts_at: DateOrDateTime = Field(
        description=(
            "Начало, ISO 8601 со смещением пояса пользователя; "
            "дата YYYY-MM-DD — встреча на весь день"
        )
    )
    ends_at: AwareDatetime | None = Field(
        default=None, description="Конец, только если названа длительность или время окончания"
    )
    participants: list[str] = Field(default_factory=list, description="Имена участников из фразы")
    remind_before_min: int | None = Field(
        default=None, ge=0, description="За сколько минут напомнить, если пользователь попросил"
    )
    notes: str | None = Field(default=None, description="Подробности, если пользователь их назвал")
    rrule: str | None = Field(
        default=None,
        description=_RRULE_HINT,
    )
    source_text: str | None = Field(default=None, description="Исходная фраза пользователя")
    search_hints: SearchHints | None = Field(
        default=None,
        description=_SEARCH_HINTS_HINT,
    )
    allow_conflict: bool = Field(
        default=False, description="true — создать несмотря на пересечение; только по явной просьбе"
    )


class ListItemsArgs(_ToolArgs):
    from_: DateOnly = Field(alias="from", description="Первый день периода, YYYY-MM-DD")
    to: DateOnly = Field(description="День после последнего (не включается), YYYY-MM-DD")
    kind: list[ItemKind] | None = Field(
        default=None,
        description="Только эти виды записей; не передавай, если пользователь не уточнил",
    )
    status: Literal["open", "done", "all"] = Field(
        default="open", description="open — невыполненные, done — выполненные, all — все"
    )


class SearchItemsArgs(_ToolArgs):
    query: str = Field(min_length=1, description="Что искать, словами пользователя")
    kind: list[ItemKind] | None = Field(
        default=None,
        description="Только эти виды записей; не передавай, если пользователь не уточнил",
    )
    from_: DateOnly | None = Field(
        default=None, alias="from", description="Искать с этого дня, YYYY-MM-DD"
    )
    to: DateOnly | None = Field(
        default=None, description="Искать до этого дня (не включается), YYYY-MM-DD"
    )
    include_done: bool = Field(default=True, description="Искать и среди выполненных")
    limit: int = Field(default=5, ge=1, le=10, description="Сколько записей вернуть")


class UpdateItemArgs(_ToolArgs):
    """Переданные поля заменяются, null очищает поле, непереданные не меняются.

    Какие поля переданы, видно по model_fields_set: так null отличается от «не передано».
    """

    id: ItemRef = Field(
        description="id записи: полный или первые 8 символов из фокуса или результата инструмента"
    )
    title: Title | None = Field(default=None, description="Новое название")
    kind: TaskKind | None = Field(
        default=None, description="Сделать задачей (task) или напоминанием (reminder)"
    )
    starts_at: DateOrDateTime | None = Field(
        default=None,
        description=(
            "Новое начало встречи или срок задачи, ISO 8601 со смещением пояса пользователя "
            "или YYYY-MM-DD"
        ),
    )
    ends_at: AwareDatetime | None = Field(
        default=None, description="Новый конец встречи, ISO 8601 со смещением пояса пользователя"
    )
    remind_at: AwareDatetime | None = Field(
        default=None,
        description="Новое время напоминания, ISO 8601 со смещением пояса пользователя",
    )
    participants: list[str] | None = Field(default=None, description="Новый список участников")
    remind_before_min: int | None = Field(
        default=None, ge=0, description="За сколько минут до встречи напомнить"
    )
    notes: str | None = Field(default=None, description="Подробности, если пользователь их назвал")
    rrule: str | None = Field(
        default=None,
        description=_RRULE_HINT,
    )
    source_text: str | None = Field(default=None, description="Исходная фраза пользователя")
    search_hints: SearchHints | None = Field(
        default=None,
        description=_SEARCH_HINTS_HINT,
    )
    allow_conflict: bool = Field(
        default=False,
        description="true — перенести несмотря на пересечение; только по явной просьбе",
    )


class CompleteItemArgs(_ToolArgs):
    id: ItemRef = Field(
        description="id записи: полный или первые 8 символов из фокуса или результата инструмента"
    )
    done: bool = Field(default=True, description="false — снять отметку о выполнении")


class DeleteItemArgs(_ToolArgs):
    id: ItemRef = Field(
        description="id записи: полный или первые 8 символов из фокуса или результата инструмента"
    )
    restore: bool = Field(default=False, description="true — восстановить удалённую запись")


class CheckConflictsArgs(_ToolArgs):
    starts_at: AwareDatetime = Field(
        description="Начало проверяемого времени, ISO 8601 со смещением пояса пользователя"
    )
    ends_at: AwareDatetime = Field(
        description="Конец проверяемого времени, ISO 8601 со смещением пояса пользователя"
    )
    exclude_id: ItemRef | None = Field(
        default=None, description="Не считать пересечением эту запись (при переносе)"
    )


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
