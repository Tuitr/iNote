"""Периоды списка «сегодня / завтра / неделя» в поясе пользователя (правило 2).

Общие для ScriptedBrain (фраза «что у меня завтра») и /v1/actions (кнопка list_period).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, Literal, TypeGuard, get_args
from zoneinfo import ZoneInfo

type Period = Literal["today", "tomorrow", "week"]

PERIODS: Final = frozenset(get_args(Period.__value__))


# Примечание: начало и конец периода в поясе пользователя; [from, to) — конец не включается
@dataclass(frozen=True)
class Range:
    start: datetime
    end: datetime


# Примечание: строка из args кнопки — один из периодов каталога
def isPeriod(value: object) -> TypeGuard[Period]:
    return isinstance(value, str) and value in PERIODS


# Примечание: границы «сегодня / завтра / неделя» от now пользователя; неделя — с понедельника
def periodRange(period: Period, now: datetime, tz: str) -> Range:
    zone = ZoneInfo(tz)
    today = now.astimezone(zone).date()
    if period == "today":
        first, days = today, 1
    elif period == "tomorrow":
        first, days = today + timedelta(days=1), 1
    else:
        first, days = today - timedelta(days=today.weekday()), 7
    return Range(start=_midnight(first, zone), end=_midnight(first + timedelta(days=days), zone))


# Примечание: полночь даты в поясе — смещение берётся у пояса на эту дату
def _midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=zone)
