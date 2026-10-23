"""Какое поле времени у записи двигать: у напоминания — remind_at, у задачи и встречи — starts_at.

Так в Item у D (main: libs/contracts/items.py, примеры J3): у напоминания starts_at = null.
Одна функция на всё — «Изменить время», «Отложить» и «перенеси её»: поменять правило в одном месте.
"""

from datetime import datetime
from typing import Final, Literal, TypeGuard, get_args

type ItemKind = Literal["task", "reminder", "event"]
type TimeField = Literal["starts_at", "remind_at"]

ITEM_KINDS: Final = frozenset(get_args(ItemKind.__value__))


# Примечание: строка из args кнопки — вид записи из перечня
def isItemKind(value: object) -> TypeGuard[ItemKind]:
    return isinstance(value, str) and value in ITEM_KINDS


# Примечание: поле времени, которое показывает «когда» и которое переносим
def timeField(kind: ItemKind | None) -> TimeField:
    return "remind_at" if kind == "reminder" else "starts_at"


# Примечание: аргументы update_item «перенести запись на moment» (ISO со смещением, правило 2)
def moveArgs(itemId: str, kind: ItemKind | None, moment: datetime) -> dict[str, object]:
    return {"id": itemId, timeField(kind): moment.isoformat()}
