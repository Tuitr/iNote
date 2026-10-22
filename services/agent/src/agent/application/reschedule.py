"""Варианты кнопки «Изменить время» без LLM: через час, завтра в это же время, завтра 9:00.

Времена считаются в поясе пользователя; вариант в прошлом, дубль другого или текущее время
записи («завтра в это же время» у записи на завтра) не предлагаем.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

MORNING: Final = time(9, 0)
IN_HOUR: Final = timedelta(hours=1)


# Примечание: вид варианта — подпись берёт renderer из шаблонов
class RescheduleChoice(StrEnum):
    IN_HOUR = "in_hour"
    SAME_TIME_TOMORROW = "same_time_tomorrow"
    TOMORROW_MORNING = "tomorrow_morning"


# Примечание: вариант переноса: вид и момент (ISO со смещением уйдёт в args кнопки)
@dataclass(frozen=True)
class RescheduleOption:
    choice: RescheduleChoice
    startsAt: datetime


# Примечание: «через час» — от «сейчас» с точностью до минуты; «завтра в это же время» — время
# записи, если оно известно, иначе текущее
def rescheduleOptions(now: datetime, tz: str, itemStart: datetime | None) -> list[RescheduleOption]:
    zone = ZoneInfo(tz)
    local = now.astimezone(zone).replace(second=0, microsecond=0)
    tomorrow = local.date() + timedelta(days=1)
    clock = (itemStart.astimezone(zone) if itemStart else local).timetz().replace(tzinfo=None)
    candidates = [
        RescheduleOption(RescheduleChoice.IN_HOUR, local + IN_HOUR),
        RescheduleOption(
            RescheduleChoice.SAME_TIME_TOMORROW, datetime.combine(tomorrow, clock, zone)
        ),
        RescheduleOption(
            RescheduleChoice.TOMORROW_MORNING, datetime.combine(tomorrow, MORNING, zone)
        ),
    ]
    options: list[RescheduleOption] = []
    for option in candidates:
        taken = [o.startsAt for o in options]
        if itemStart is not None:
            taken.append(itemStart)
        if option.startsAt > local and option.startsAt not in taken:
            options.append(option)
    return options
