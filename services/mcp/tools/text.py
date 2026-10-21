"""Короткие строки для модели (content[0].text в ответе инструмента, J3).

Формат утверждён Никитой до ответа B (журнал решений 2026-10-21): у записи короткий id в скобках,
дата — абсолютная («чт, 22 октября»), а не «завтра»: модель и так знает «сейчас» из TurnInput.now.
"""

from datetime import date, datetime
from uuid import UUID

from libs.contracts.items import Item, ItemBrief
from libs.contracts.tools import TimeSlot

_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def short_id(item_id: UUID) -> str:
    """Первые 8 символов id: инструменты принимают их вместо полного UUID (J3)."""
    return str(item_id)[:8]


def day_label(day: date) -> str:
    return f"{_WEEKDAYS[day.weekday()]}, {day.day} {_MONTHS[day.month - 1]}"


def created_text(item: Item) -> str:
    return item_line("Создано", item)


def item_line(prefix: str, item: Item) -> str:
    """«Изменено: [7f3a9c2e] чт, 22 октября 12:00 Позвонить Ивану»."""
    return f"{prefix}: [{short_id(item.id)}] {_with_when(item)}"


def list_text(items: list[Item], truncated: bool) -> str:
    """Заголовок дня, под ним по строке на запись: «[7f3a9c2e] 15:00–16:00 Встреча с Иваном»."""
    if not items:
        return "Записей нет"
    lines: list[str] = []
    current_day: date | None = None
    for item in items:
        # В список за период попадают только записи со временем (ItemRepo.list_in_range).
        moment = item.starts_at or item.remind_at
        if moment.date() != current_day:
            current_day = moment.date()
            lines.append(day_label(current_day))
        lines.append(f"[{short_id(item.id)}] {_clock_part(item, moment)} {item.title}")
    if truncated:
        lines.append(f"Показаны первые {len(items)}, есть ещё: сузь период.")
    return "\n".join(lines)


def error_text(code: str, message: str) -> str:
    # КиП: модель видит «Ошибка past_time: время уже прошло».
    return f"Ошибка {code}: {message[:1].lower()}{message[1:]}"


def _when(item: Item) -> str:
    """«чт, 22 октября 10:00», «… 15:00–16:00», «… весь день» или пусто, если срока нет."""
    moment = item.starts_at or item.remind_at
    if moment is None:
        return ""
    return f"{day_label(moment.date())} {_clock_part(item, moment)}"


def _clock_part(item: Item, moment: datetime) -> str:
    """«10:00», «15:00–16:00» или «весь день»; moment — starts_at или remind_at записи."""
    if item.all_day:
        return "весь день"
    if item.ends_at is not None:
        return f"{moment:%H:%M}–{item.ends_at:%H:%M}"
    return f"{moment:%H:%M}"


def conflict_text(prefix: str, conflicts: list[ItemBrief], free_slots: list[TimeSlot]) -> str:
    """«Не создано: пересекается с «Встреча с дизайнером» в 15:00. Свободно: 16:00–17:00.»"""
    # В ItemBrief нет конца встречи (контракт КиП), поэтому у пересечений только время начала.
    busy = ", ".join(f"«{brief.title}» в {_clock(brief.starts_at)}" for brief in conflicts)
    if free_slots:
        free = ", ".join(f"{slot.starts_at:%H:%M}–{slot.ends_at:%H:%M}" for slot in free_slots)
        return f"{prefix}: пересекается с {busy}. Свободно: {free}."
    return f"{prefix}: пересекается с {busy}. Свободных окон в этот день нет."


def _clock(moment: datetime | None) -> str:
    return "" if moment is None else f"{moment:%H:%M}"


def search_text(items: list[Item]) -> str:
    """«Найдено: [7f3a9c2e] вс, 18 октября 15:00–16:00 Встреча с Иваном»; несколько — по строке."""
    lines = [f"[{short_id(item.id)}] {_with_when(item)}" for item in items]
    if not lines:
        return "Ничего не найдено"
    if len(lines) == 1:
        return f"Найдено: {lines[0]}"
    return "Найдено:\n" + "\n".join(lines)


def _with_when(item: Item) -> str:
    when = _when(item)
    return f"{when} {item.title}" if when else item.title
