"""«Отложить на N минут»: новый момент напоминания в поясе пользователя (Clock хода).

«На N минут» — от «сейчас» с точностью до минуты.
"""

from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

MAX_SNOOZE_MINUTES: Final = 24 * 60


# Примечание: «отложить на minutes» от «сейчас» пользователя, секунды отбрасываются
def snoozeFor(now: datetime, tz: str, minutes: int) -> datetime:
    local = now.astimezone(ZoneInfo(tz)).replace(second=0, microsecond=0)
    return local + timedelta(minutes=minutes)
