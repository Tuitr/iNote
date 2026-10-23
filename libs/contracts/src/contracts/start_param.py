"""J6 · bot → max_app: start_param кнопки мини-приложения. C → E, к заморозке G4."""

import re
import uuid
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Annotated, Final, Self, override

from pydantic import AfterValidator

START_PARAM_MAX_LENGTH: Final = 512
_ALPHABET_RE: Final = re.compile(r"^[A-Za-z0-9_-]+$")
_DATED_RE: Final = re.compile(r"^(day|week)_(\d{4}-\d{2}-\d{2})$")
_ITEM_RE: Final = re.compile(
    r"^item_([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
)


# Примечание: какой экран открыть в мини-приложении
class StartParamKind(StrEnum):
    TODAY = "today"
    DAY = "day"
    WEEK = "week"
    ITEM = "item"
    DIALOG = "dialog"


# Примечание: разобранный start_param; str() даёт строку для кнопки open_app
@dataclass(frozen=True)
class StartParam:
    kind: StartParamKind
    day: date | None = None
    itemId: uuid.UUID | None = None

    # Примечание: у day/week обязательна дата, у item — id; иначе строку не собрать
    def __post_init__(self) -> None:
        needsDay = self.kind in (StartParamKind.DAY, StartParamKind.WEEK)
        if needsDay != (self.day is not None):
            msg = f"start_param {self.kind}: дата {'обязательна' if needsDay else 'не нужна'}"
            raise ValueError(msg)
        if (self.kind is StartParamKind.ITEM) != (self.itemId is not None):
            msg = f"start_param {self.kind}: id записи только для item"
            raise ValueError(msg)

    # Примечание: каноничная строка: today, dialog, day_2026-10-21, week_2026-10-19, item_<uuid>
    @override
    def __str__(self) -> str:
        match self.kind:
            case StartParamKind.DAY | StartParamKind.WEEK if self.day is not None:
                return f"{self.kind.value}_{self.day.isoformat()}"
            case StartParamKind.ITEM:
                return f"item_{self.itemId}"
            case _:
                return self.kind.value

    # Примечание: экран «сегодня»
    @classmethod
    def today(cls) -> Self:
        return cls(StartParamKind.TODAY)

    # Примечание: экран диалога с ассистентом
    @classmethod
    def dialog(cls) -> Self:
        return cls(StartParamKind.DIALOG)

    # Примечание: конкретный день
    @classmethod
    def forDay(cls, day: date) -> Self:
        return cls(StartParamKind.DAY, day=day)

    # Примечание: неделя по её первому дню (неделя с понедельника — правило 2 CLAUDE.md)
    @classmethod
    def forWeek(cls, monday: date) -> Self:
        if monday.weekday() != 0:
            msg = f"неделя начинается с понедельника, получено {monday.isoformat()}"
            raise ValueError(msg)
        return cls(StartParamKind.WEEK, day=monday)

    # Примечание: карточка записи
    @classmethod
    def forItem(cls, itemId: uuid.UUID) -> Self:
        return cls(StartParamKind.ITEM, itemId=itemId)


# Примечание: строгий разбор J6: алфавит, длина, форма, реальная дата, UUID в нижнем регистре
def parseStartParam(raw: str) -> StartParam:
    if not raw or len(raw) > START_PARAM_MAX_LENGTH or not _ALPHABET_RE.fullmatch(raw):
        msg = f"start_param: только [A-Za-z0-9_-], 1–{START_PARAM_MAX_LENGTH} символов"
        raise ValueError(msg)
    if raw in (StartParamKind.TODAY, StartParamKind.DIALOG):
        return StartParam(StartParamKind(raw))
    if dated := _DATED_RE.fullmatch(raw):
        kind, value = dated.groups()
        try:
            day = date.fromisoformat(value)
        except ValueError as exc:
            msg = f"start_param: несуществующая дата {value}"
            raise ValueError(msg) from exc
        return StartParam.forWeek(day) if kind == "week" else StartParam.forDay(day)
    if item := _ITEM_RE.fullmatch(raw):
        return StartParam.forItem(uuid.UUID(item.group(1)))
    msg = f"start_param: неизвестный формат {raw!r}"
    raise ValueError(msg)


# Примечание: проверка без исключения — для фронта и логов
def isValidStartParam(raw: str) -> bool:
    try:
        parseStartParam(raw)
    except ValueError:
        return False
    return True


# Примечание: валидатор для pydantic — строка остаётся строкой, но только каноничной формы J6
def _validateStartParam(raw: str) -> str:
    parsed = parseStartParam(raw)
    if str(parsed) != raw:
        msg = f"start_param не в каноничной форме: {raw!r} (ожидается {parsed})"
        raise ValueError(msg)
    return raw


type StartParamStr = Annotated[str, AfterValidator(_validateStartParam)]
