"""Перевод времени между UTC и поясом пользователя.

В БД время хранится в UTC, наружу уходит в поясе пользователя (КиП, «Данные»).
Время без смещения (naive) не принимается: по нему нельзя понять, какой это момент.
"""

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo


def now_utc() -> datetime:
    return datetime.now(UTC)


def to_utc(value: datetime) -> datetime:
    _require_aware(value)
    return value.astimezone(UTC)


def to_user_tz(value: datetime, tz: str) -> datetime:
    _require_aware(value)
    return value.astimezone(ZoneInfo(tz))


def local_midnight_utc(day: date, tz: str) -> datetime:
    """Начало дня day в поясе tz, выраженное в UTC.

    Если в этом поясе полночь пропадает из-за перевода часов, получается момент
    перевода: с него день и начинается.
    """
    return local_time_utc(day, time.min, tz)


def local_time_utc(day: date, clock: time, tz: str) -> datetime:
    """Момент «day, clock по часам пояса tz», выраженный в UTC."""
    return datetime.combine(day, clock, tzinfo=ZoneInfo(tz)).astimezone(UTC)


def local_today(tz: str) -> date:
    """Сегодняшняя дата по часам пояса tz."""
    return to_user_tz(now_utc(), tz).date()


def days_range_utc(start: date, end: date, tz: str) -> tuple[datetime, datetime]:
    """Полуинтервал [start, end) из дат пользователя в моменты UTC: end не включается."""
    return local_midnight_utc(start, tz), local_midnight_utc(end, tz)


def _require_aware(value: datetime) -> None:
    if value.utcoffset() is None:
        raise ValueError("Время без часового пояса")
