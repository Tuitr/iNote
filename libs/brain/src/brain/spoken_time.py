"""Момент из фразы («в 5», «послезавтра в 10», «через неделю в 9 вечера») считает код, а не LLM.

GigaChat ошибается в арифметике дат: ночью «в 5» ставил на завтра 17:00, вечером «через неделю
в 9» — на день позже. Если фраза укладывается в простые правила ниже, момент берётся отсюда и
подменяет тот, что дала модель; иначе (день недели, дата, «через 2 часа», несколько времён)
решает модель.

- День: «сегодня», «завтра» (ночью, до 04:00 — наступающий день, то есть сегодняшняя дата),
  «послезавтра», «через N дней», «через неделю», «через N недель».
- Время: одно «в H» или «в H:MM», можно с «утра / дня / вечера / ночи».
- Без дня: ближайший ещё не наступивший момент — H:MM или (H+12):MM сегодня, иначе завтра.
- День назван, части суток нет: 7–11 — утро, 12 — полдень, 1–6 — день (13:00–18:00).
- День назван, а момент уже прошёл («сегодня в 7» в 23:42) — None: про прошлое говорит модель.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

HALF_DAY_HOURS: Final = 12
DAYS_IN_WEEK: Final = 7
# ночью «завтра» — это наступающий день: до этого часа «завтра» = сегодняшняя дата
NIGHT_ENDS_HOUR: Final = 4
MORNING_FIRST_HOUR: Final = 7
LATEST_NIGHT_HOUR: Final = 5
NUMBER_WORDS: Final = {
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
}
_TIME_RE: Final = re.compile(
    r"(?<!\w)в\s+(\d{1,2})(?::(\d{2}))?(?:\s+час\w*)?"
    r"(?:\s+(утра|утром|дня|днём|днем|вечера|вечером|ночи|ночью))?(?!\s*(?:\d|%|минут|раз|руб))"
)
# «через неделю», «через 3 дня», «через две недели»
_IN_RE: Final = re.compile(r"через\s+(?:(\d{1,2}|[а-яё]+)\s+)?(день|дн[яей]\w*|недел\w*)")
# то, что код не разбирает: дни недели, даты, месяцы, «через час», части суток без часа
_UNSUPPORTED_RE: Final = re.compile(
    r"понедельник|вторник|\bсред[уыае]\b|четверг|пятниц|суббот|воскресень|выходн|месяц|январ|"
    r"феврал|\bмарт|апрел|\bма[яй]\b|июн|июл|август|сентябр|октябр|ноябр|декабр|числ|"
    r"\d{1,2}[./]\d{1,2}|полдень|полночь|следующ|эт(?:ой|у)\s+недел"
)


# Примечание: момент и назван ли день — при незавершённом вопросе дата бывает только в нём
@dataclass(frozen=True)
class Spoken:
    moment: datetime
    dayNamed: bool


# Примечание: день из фразы не разобран кодом — решает модель
class _UnparsedDayError(Exception):
    pass


# Примечание: момент из фразы по правилам модуля или None — тогда решает модель
def spokenMoment(text: str, now: datetime, tz: str) -> Spoken | None:
    lowered = text.lower()
    times = list(_TIME_RE.finditer(lowered))
    if _UNSUPPORTED_RE.search(lowered) or len(times) != 1:
        return None
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    try:
        said = time(int(times[0][1]), int(times[0][2] or 0))
        offset = _dayOffset(lowered, local)
    except (ValueError, _UnparsedDayError):
        return None
    part = times[0][3]
    if offset is None:
        return Spoken(_nearest(said, part, local, zone), dayNamed=False)
    day = local.date() + timedelta(days=offset)
    moment = _at(day, _namedDayHour(said.hour, part), said.minute, zone)
    return Spoken(moment, dayNamed=True) if moment > local else None


# Примечание: смещение дня от сегодня; None — день не назван; не разобран — _UnparsedDayError
def _dayOffset(text: str, local: datetime) -> int | None:
    found: list[int] = []
    if re.search(r"\bсегодня\b", text):
        found.append(0)
    if re.search(r"\bпослезавтра\b", text):
        found.append(2)
    elif re.search(r"\bзавтра\b", text):
        found.append(0 if local.hour < NIGHT_ENDS_HOUR else 1)
    periods = list(_IN_RE.finditer(text))
    if text.count("через") != len(periods):
        raise _UnparsedDayError
    for match in periods:
        count = _count(match[1])
        found.append(count * DAYS_IN_WEEK if match[2].startswith("недел") else count)
    if len(found) > 1:
        raise _UnparsedDayError
    return found[0] if found else None


# Примечание: «через неделю» — 1, «через 3 дня» — 3, «через две недели» — 2
def _count(word: str | None) -> int:
    if word is None:
        return 1
    if word.isdigit():
        return int(word)
    if word in NUMBER_WORDS:
        return NUMBER_WORDS[word]
    raise _UnparsedDayError


# Примечание: час с явной частью суток; без неё — по правилу «день назван»
def _namedDayHour(hour: int, part: str | None) -> int:
    if part is not None:
        return _partHour(hour, part)
    if hour == 0 or hour >= MORNING_FIRST_HOUR:
        return hour
    return hour + HALF_DAY_HOURS


# Примечание: «5 утра» — 05, «5 вечера» — 17, «12 дня» — 12, «2 ночи» — 02, «11 ночи» — 23
def _partHour(hour: int, part: str) -> int:
    if hour > HALF_DAY_HOURS:
        return hour
    if part.startswith(("утр", "ноч")) and hour == HALF_DAY_HOURS:
        return 0
    if part.startswith("утр") or (part.startswith("ноч") and hour <= LATEST_NIGHT_HOUR):
        return hour
    return hour if hour == HALF_DAY_HOURS else hour + HALF_DAY_HOURS


# Примечание: день не назван — ближайший ещё не наступивший вариант сегодня или завтра
def _nearest(said: time, part: str | None, local: datetime, zone: ZoneInfo) -> datetime:
    if part is not None:
        hours = {_partHour(said.hour, part)}
    elif 1 <= said.hour <= HALF_DAY_HOURS:
        hours = {said.hour, (said.hour + HALF_DAY_HOURS) % 24}
    else:
        hours = {said.hour}
    today = local.date()
    candidates = [
        _at(day, hour, said.minute, zone)
        for day in (today, today + timedelta(days=1))
        for hour in hours
    ]
    return min(moment for moment in candidates if moment > local)


def _at(day: date, hour: int, minute: int, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=zone)
