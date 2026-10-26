"""Пояса для /settings: белый список российских поясов IANA (S3, шаг 5).

Порядок — с запада на восток, как кнопки. Другой пояс из кнопки — validation_error.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo


# Примечание: пояс и подпись города на кнопке
@dataclass(frozen=True)
class RuTimezone:
    tz: str
    city: str


RU_TIMEZONES: Final = (
    RuTimezone("Europe/Kaliningrad", "Калининград"),
    RuTimezone("Europe/Moscow", "Москва"),
    RuTimezone("Europe/Samara", "Самара"),
    RuTimezone("Asia/Yekaterinburg", "Екатеринбург"),
    RuTimezone("Asia/Omsk", "Омск"),
    RuTimezone("Asia/Novosibirsk", "Новосибирск"),
    RuTimezone("Asia/Krasnoyarsk", "Красноярск"),
    RuTimezone("Asia/Irkutsk", "Иркутск"),
    RuTimezone("Asia/Yakutsk", "Якутск"),
    RuTimezone("Asia/Vladivostok", "Владивосток"),
    RuTimezone("Asia/Magadan", "Магадан"),
    RuTimezone("Asia/Kamchatka", "Камчатка"),
)
_BY_TZ: Final = {zone.tz: zone for zone in RU_TIMEZONES}
_SECONDS_PER_HOUR: Final = 3600
_SECONDS_PER_MINUTE: Final = 60


# Примечание: пояс из белого списка или None (кнопка устарела или подделана)
def ruTimezone(tz: object) -> RuTimezone | None:
    return _BY_TZ.get(tz) if isinstance(tz, str) else None


# Примечание: «UTC+5», «UTC+3», «UTC+5:30» — смещение пояса в момент at
def utcOffset(tz: str, at: datetime) -> str:
    offset = at.astimezone(ZoneInfo(tz)).utcoffset()
    seconds = int(offset.total_seconds()) if offset is not None else 0
    sign = "+" if seconds >= 0 else "-"
    hours, rest = divmod(abs(seconds), _SECONDS_PER_HOUR)
    minutes = rest // _SECONDS_PER_MINUTE
    return f"UTC{sign}{hours}" + (f":{minutes:02d}" if minutes else "")
