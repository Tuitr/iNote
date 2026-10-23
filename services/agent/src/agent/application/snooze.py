"""«Отложить на N минут» и «Завтра»: новый момент напоминания в поясе пользователя (Clock хода).

«На N минут» — от «сейчас» с точностью до минуты. «Завтра» — то же время суток завтра: время
напоминания, если запись в фокусе и время известно, иначе — «сейчас» (кнопку жмут в момент
напоминания). Переход на летнее/зимнее время — по стенным часам пояса (предложение к G3).
"""

from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

MAX_SNOOZE_MINUTES: Final = 24 * 60


# Примечание: «отложить на minutes» от «сейчас» пользователя, секунды отбрасываются
def snoozeFor(now: datetime, tz: str, minutes: int) -> datetime:
    local = now.astimezone(ZoneInfo(tz)).replace(second=0, microsecond=0)
    return local + timedelta(minutes=minutes)


# Примечание: «завтра в то же время»: стенное время напоминания (или «сейчас») на следующий день
def snoozeTomorrow(now: datetime, tz: str, remindAt: datetime | None) -> datetime:
    zone = ZoneInfo(tz)
    base = (remindAt or now).astimezone(zone)
    day = now.astimezone(zone).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, base.hour, base.minute, tzinfo=zone)
