"""Дата и время из фразы пользователя — разбор кодом, а не LLM.

GigaChat ошибается в арифметике дат и сам выбирает там, где надо спросить: ставил «завтра»
без названного дня, 02:50 вместо «утра или вечера?», 13:00 из «01.13», среду вместо вторника для
«следующего дня». Здесь фраза разбирается в структуру: день (дата), время (с признаком «понятно,
утро это или вечер») и проблема, о которой надо спросить. Считает момент и задаёт вопросы
time_guard.py.

- День: «сегодня», «завтра» (всегда следующая календарная дата, и ночью тоже),
  «послезавтра», «следующий день», «через N дней / недель / месяцев», день недели, «1 мая»,
  «03.06.2027».
  Дата без года — ближайшая будущая. Несуществующая дата (01.13, 31.02) — проблема, не время.
- Время: «в 5», «в 17:30», «на десять пятнадцать», «без десяти 3», «пол третьего», «в обед».
  Час 1–12 без «утра / дня / вечера / ночи» — неоднозначно (спросить); 0, 13–23 и «09:30» с
  ведущим нулём — однозначно.
- Противоречие («в мае 03.06.2027», «в пятницу 28.10», две разные даты) — проблема.
- «Через 2 часа», «на следующей неделе», «в мае» без числа — supported=False: решает модель.
- Смещение напоминания: «за 40 минут», «за час», «за полчаса», «за 2 часа», «за день» — насколько
  раньше начала записи напомнить (remindOffset).
"""

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

HALF_DAY_HOURS: Final = 12
DAYS_IN_WEEK: Final = 7
MINUTES_IN_HOUR: Final = 60
LATEST_NIGHT_HOUR: Final = 5
SHORT_YEAR_BASE: Final = 2000
# среди любых 5 лет подряд есть високосный (кроме вековых — для дат бота неважно)
LEAP_YEARS_SPAN: Final = 5
LUNCH: Final = (13, 0)

_UNITS: Final = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
    "одну": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
}
_TEENS_AND_TENS: Final = {
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
}
_TENS: Final = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50}
# «без десяти», «без пяти», «без четверти»
_GENITIVE: Final = {
    "одной": 1,
    "двух": 2,
    "трех": 3,
    "пяти": 5,
    "десяти": 10,
    "пятнадцати": 15,
    "четверти": 15,
    "двадцати": 20,
    "двадцати пяти": 25,
}
# «пол третьего», «половина первого»
_ORDINALS: Final = {
    "первого": 1,
    "второго": 2,
    "третьего": 3,
    "четвертого": 4,
    "пятого": 5,
    "шестого": 6,
    "седьмого": 7,
    "восьмого": 8,
    "девятого": 9,
    "десятого": 10,
    "одиннадцатого": 11,
    "двенадцатого": 12,
}
_MONTHS_GENITIVE: Final = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_MONTHS_PREPOSITIONAL: Final = {
    "январе": 1,
    "феврале": 2,
    "марте": 3,
    "апреле": 4,
    "мае": 5,
    "июне": 6,
    "июле": 7,
    "августе": 8,
    "сентябре": 9,
    "октябре": 10,
    "ноябре": 11,
    "декабре": 12,
}
_WEEKDAYS: Final = {
    "понедельник": 0,
    "вторник": 1,
    "среду": 2,
    "среда": 2,
    "четверг": 3,
    "пятницу": 4,
    "пятница": 4,
    "субботу": 5,
    "суббота": 5,
    "воскресенье": 6,
}
WEEKDAY_NAMES: Final = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
MONTH_NAMES: Final = tuple(_MONTHS_GENITIVE)

_NUMERIC_DATE_RE: Final = re.compile(
    r"(?<![\d:])(\d{1,2})[./](\d{1,2})(?:[./](\d{4}|\d{2}))?(?![\d:])"
)
_WORD_DATE_RE: Final = re.compile(
    r"(?<!\d)(\d{1,2})\s+(" + "|".join(_MONTHS_GENITIVE) + r")(?:\s+(\d{4}))?"
)
_MONTH_ONLY_RE: Final = re.compile(r"\bв\s+(" + "|".join(_MONTHS_PREPOSITIONAL) + r")\b")
_WEEKDAY_RE: Final = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b")
_IN_DAYS_RE: Final = re.compile(
    r"через\s+(?:(\d{1,2})\s+)?(день|дня|дней|неделю|недели|недель|месяц|месяца|месяцев)\b"
)
_NEXT_DAY_RE: Final = re.compile(r"следующ\w*\s+(?:день|дня|дню)")
# относительное время и периоды — их разбирает модель
_LEFT_TO_MODEL_RE: Final = re.compile(
    r"через\s+(?:\d+\s+|пару\s+|полчаса|полтора)?(?:час|минут|секунд|полчаса|полтора)|"
    r"следующ\w*\s+(?:недел|месяц|год|понедельник|вторник|сред|четверг|пятниц|суббот|воскрес)|"
    r"эт(?:ой|у)\s+недел|выходн|через\s+пару"
)
_PART_RE: Final = r"(утра|утром|дня|днем|вечера|вечером|ночи|ночью)"
_BEFORE_RE: Final = re.compile(
    r"\bбез\s+(двадцати пяти|" + "|".join(_GENITIVE) + r"|\d{1,2})(?:\s+минут\w*)?\s+(\d{1,2})"
    r"(?:\s+час\w*)?(?:\s+" + _PART_RE + ")?"
)
_HALF_RE: Final = re.compile(
    r"\bпол(?:овин[аеуы])?\s*-?\s*(" + "|".join(_ORDINALS) + r")(?:\s+" + _PART_RE + ")?"
)
_CLOCK_RE: Final = re.compile(r"(?<![\d.])(\d{1,2}):(\d{2})(?!\d)(?:\s+" + _PART_RE + ")?")
# «в 5», «в 3 часа дня», «на 10 15»; не «на 5 минут», «в 3 раза», «в 10%», «в 10.15»
_AT_RE: Final = re.compile(
    r"\b(?:в|на)\s+(\d{1,2})(?![./:\d])(?!\s*(?:минут|раз|руб|%|недел|числ|год|лет))"
    r"(?:\s+час\w*)?(?:\s+(\d{1,2})(?![./:\d]))?(?:\s+минут\w*)?(?:\s+" + _PART_RE + ")?"
)
_NAMED_RE: Final = re.compile(r"\b(?:в\s+)?(обед|полдень|полночь)\b")
_OFFSET_RE: Final = re.compile(
    r"\bза\s+(?:(\d{1,4})\s+)?(пол\s*часа|минут\w*|мин|час\w*|день|дня|дней|сутки|суток)\b"
)
_OFFSET_UNITS: Final = (
    ("пол", timedelta(minutes=30)),
    ("мин", timedelta(minutes=1)),
    ("час", timedelta(hours=1)),
)
_PART_ANYWHERE_RE: Final = re.compile(r"\b(утром|днем|вечером|ночью)\b")


# Примечание: время из фразы; isClear — утро это или вечер, понятно без вопроса
@dataclass(frozen=True)
class SaidTime:
    hour: int
    minute: int
    isClear: bool

    # Примечание: два варианта неоднозначного часа: утро (или ночь) и вечер (или день)
    def variants(self) -> tuple[tuple[int, int], tuple[int, int]]:
        morning = self.hour % HALF_DAY_HOURS
        return (morning, self.minute), (morning + HALF_DAY_HOURS, self.minute)


# Примечание: о чём спросить вместо догадки: вопрос и варианты-кнопки
@dataclass(frozen=True)
class Problem:
    question: str
    options: list[str] = field(default_factory=list)


# Примечание: итог разбора; supported=False — фраза сложнее правил модуля, решает модель
@dataclass(frozen=True)
class Spoken:
    day: date | None = None
    time: SaidTime | None = None
    problem: Problem | None = None
    supported: bool = True
    # «в мае» без числа: месяц назван, день — нет
    month: int | None = None


# Примечание: разбор фразы относительно «сейчас» пользователя
def parseSpoken(text: str, now: datetime, tz: str) -> Spoken:
    normalized = normalize(text)
    if _LEFT_TO_MODEL_RE.search(normalized):
        return Spoken(supported=False)
    times = _times(normalized)
    # время сохраняется и при проблеме с датой: ответ датой дополнит просьбу с этим временем
    said = times[0] if len(times) == 1 else None
    monthOnly = _MONTH_ONLY_RE.search(normalized)
    if monthOnly and not (_NUMERIC_DATE_RE.search(normalized) or _WORD_DATE_RE.search(normalized)):
        return Spoken(time=said, month=_MONTHS_PREPOSITIONAL[monthOnly[1]])
    local = now.astimezone(ZoneInfo(tz))
    days, problem = _days(normalized, local)
    if problem is not None:
        return Spoken(time=said, problem=problem)
    if len(set(days)) > 1:
        return Spoken(time=said, problem=_dateConflict(sorted(set(days))))
    return Spoken(day=days[0] if days else None, time=said, supported=len(times) <= 1)


# Примечание: «напомни за 40 минут» — насколько раньше начала записи напомнить; нет — None
def remindOffset(text: str) -> timedelta | None:
    match = _OFFSET_RE.search(normalize(text))
    if match is None:
        return None
    count = int(match[1]) if match[1] else 1
    unit = next((step for prefix, step in _OFFSET_UNITS if match[2].startswith(prefix)), None)
    return count * (unit or timedelta(days=1))


# Примечание: фраза без времени суток (для задачи: её срок — дата на весь день, время не нужно,
# а модель на «в 5» спрашивала «утра или вечера?»); нормализованный текст
def withoutTime(text: str) -> str:
    # предлог перед «15:00» — вместе со временем
    normalized = re.sub(r"\b(?:в|на|к)\s+(?=\d{1,2}:\d{2})", "", normalize(text))
    for pattern in (_NAMED_RE, _BEFORE_RE, _HALF_RE, _CLOCK_RE, _AT_RE):
        normalized = pattern.sub(" ", normalized)
    return " ".join(normalized.split())


# Примечание: нижний регистр, «ё» → «е», числительные → цифры («десять пятнадцать» → «10 15»)
def normalize(text: str) -> str:
    words = text.lower().replace("ё", "е").replace("-", " ").split()
    result: list[str] = []
    index = 0
    while index < len(words):
        word = words[index].strip(",!?;")
        following = words[index + 1].strip(",!?;") if index + 1 < len(words) else ""
        if word == "после" and following == "завтра":
            # «после завтра» раздельно (так пишут и так распознаёт речь) = «послезавтра»
            result.append("послезавтра")
            index += 2
            continue
        if word in _TENS:
            value = _TENS[word]
            if index + 1 < len(words) and words[index + 1].strip(",!?;") in _UNITS:
                value += _UNITS[words[index + 1].strip(",!?;")]
                index += 1
            result.append(str(value))
        elif word in _TEENS_AND_TENS:
            result.append(str(_TEENS_AND_TENS[word]))
        elif word in _UNITS and not _isGenitiveContext(result):
            result.append(str(_UNITS[word]))
        else:
            result.append(word)
        index += 1
    return " ".join(result)


# Примечание: после «без» числительные — в родительном падеже, их разбирает _BEFORE_RE
def _isGenitiveContext(previous: list[str]) -> bool:
    return bool(previous) and previous[-1] == "без"


# Примечание: все дни из фразы (даты, относительные дни, дни недели) или проблема с датой
def _days(text: str, local: datetime) -> tuple[list[date], Problem | None]:
    today = local.date()
    found: list[date] = []
    monthOnly = [_MONTHS_PREPOSITIONAL[m[1]] for m in _MONTH_ONLY_RE.finditer(text)]
    explicit: list[tuple[date, str]] = []
    for match in _NUMERIC_DATE_RE.finditer(text):
        parsed = _date(int(match[1]), int(match[2]), match[3], today)
        if parsed is None:
            return [], Problem(f"Такой даты нет: «{match[0]}». Какую дату ты имел в виду?")
        explicit.append((parsed, match[0]))
    for match in _WORD_DATE_RE.finditer(text):
        parsed = _date(int(match[1]), _MONTHS_GENITIVE[match[2]], match[3], today)
        if parsed is None:
            return [], Problem(f"Такой даты нет: «{match[0]}». Какую дату ты имел в виду?")
        explicit.append((parsed, match[0]))
    for day, written in explicit:
        if day < today:
            return [], Problem(f"Дата {written} уже прошла. На какой день поставить?")
        if monthOnly and day.month not in monthOnly:
            month = monthLabel(monthOnly[0])
            question = f"Уточни дату: в {month} или {humanDate(day)}?"
            return [], Problem(question, [f"в {month}", humanDate(day)])
        found.append(day)
    weekdays = [_WEEKDAYS[m[1]] for m in _WEEKDAY_RE.finditer(text)]
    for weekday in weekdays:
        if explicit and explicit[0][0].weekday() != weekday:
            day = explicit[0][0]
            label = f"{WEEKDAY_NAMES[weekday]}"
            question = f"Уточни день: {_weekdayLabel(weekday)} или {humanDate(day)}?"
            return [], Problem(question, [label, humanDate(day)])
        found.append(today + timedelta(days=(weekday - today.weekday()) % DAYS_IN_WEEK))
    found.extend(_relativeDays(text, local))
    return found, None


# Примечание: «сегодня», «завтра», «послезавтра», «следующий день», «через N дней / недель»
def _relativeDays(text: str, local: datetime) -> list[date]:
    today = local.date()
    found: list[date] = []
    if re.search(r"\bсегодня\b", text):
        found.append(today)
    if re.search(r"\bпослезавтра\b", text):
        found.append(today + timedelta(days=2))
    elif re.search(r"\bзавтра\b", text):
        found.append(today + timedelta(days=1))
    if _NEXT_DAY_RE.search(text):
        found.append(today + timedelta(days=1))
    for match in _IN_DAYS_RE.finditer(text):
        count = int(match[1]) if match[1] else 1
        if match[2].startswith("месяц"):
            found.append(_addMonths(today, count))
            continue
        days = count * DAYS_IN_WEEK if match[2].startswith("недел") else count
        found.append(today + timedelta(days=days))
    return found


# Примечание: «через месяц» — то же число через N месяцев; 31-е в коротком месяце — последний день
def _addMonths(today: date, count: int) -> date:
    index = today.month - 1 + count
    year, month = today.year + index // len(MONTH_NAMES), index % len(MONTH_NAMES) + 1
    return date(year, month, min(today.day, calendar.monthrange(year, month)[1]))


# Примечание: ближайшая будущая дата с этим числом и месяцем; не существует — None
def nearestDate(day: int, month: int, today: date) -> date | None:
    return _date(day, month, None, today)


# Примечание: дата из дня, месяца и года; без года — ближайшая будущая; не существует — None
def _date(day: int, month: int, rawYear: str | None, today: date) -> date | None:
    if not 1 <= month <= len(MONTH_NAMES):
        return None
    if rawYear is not None:
        year = int(rawYear) + (SHORT_YEAR_BASE if len(rawYear) == 2 else 0)  # noqa: PLR2004
        return _safeDate(year, month, day)
    # 29 февраля без года — ближайший високосный; остальное — этот год или следующий
    for year in range(today.year, today.year + LEAP_YEARS_SPAN):
        candidate = _safeDate(year, month, day)
        if candidate is not None and candidate >= today:
            return candidate
    return None


def _safeDate(year: int, month: int, day: int) -> date | None:
    if not 1 <= day <= calendar.monthrange(year, month)[1]:
        return None
    return date(year, month, day)


# Примечание: все времена во фразе; утро/вечер — по явной части суток или формату
def _times(text: str) -> list[SaidTime]:
    found: list[SaidTime] = []
    spans: list[tuple[int, int]] = []

    def add(said: SaidTime | None, span: tuple[int, int]) -> None:
        if said is not None and not any(s <= span[0] < e for s, e in spans):
            found.append(said)
            spans.append(span)

    anywhere = _PART_ANYWHERE_RE.search(text)
    partAnywhere = anywhere[1] if anywhere else None
    for match in _NAMED_RE.finditer(text):
        hour, minute = {"обед": LUNCH, "полдень": (12, 0), "полночь": (0, 0)}[match[1]]
        add(SaidTime(hour, minute, isClear=True), match.span())
    for match in _BEFORE_RE.finditer(text):
        raw = match[1]
        minutes = int(raw) if raw.isdigit() else _GENITIVE[raw]
        hour = (int(match[2]) - 1) % 24
        add(_said(hour, MINUTES_IN_HOUR - minutes, match[3] or partAnywhere), match.span())
    for match in _HALF_RE.finditer(text):
        hour = (_ORDINALS[match[1]] - 1) % HALF_DAY_HOURS
        add(_said(hour or HALF_DAY_HOURS, 30, match[2] or partAnywhere), match.span())
    for match in _CLOCK_RE.finditer(text):
        written = match[1]
        said = _said(int(written), int(match[2]), match[3] or partAnywhere)
        if said is not None and written.startswith("0") and len(written) == 2:  # noqa: PLR2004
            said = SaidTime(said.hour, said.minute, isClear=True)
        add(said, match.span())
    for match in _AT_RE.finditer(text):
        add(_said(int(match[1]), int(match[2] or 0), match[3] or partAnywhere), match.span())
    return found


# Примечание: час и минуты с частью суток; 0 и 13–23 — понятно и так; вне диапазона — None
def _said(hour: int, minute: int, part: str | None) -> SaidTime | None:
    if not 0 <= hour <= 23 or not 0 <= minute < MINUTES_IN_HOUR:  # noqa: PLR2004
        return None
    if hour == 0 or hour > HALF_DAY_HOURS:
        return SaidTime(hour, minute, isClear=True)
    if part is None:
        return SaidTime(hour % HALF_DAY_HOURS if hour == HALF_DAY_HOURS else hour, minute, False)
    return SaidTime(_partHour(hour, part), minute, isClear=True)


# Примечание: «5 утра» — 05, «5 вечера» — 17, «12 дня» — 12, «2 ночи» — 02, «11 ночи» — 23
def _partHour(hour: int, part: str) -> int:
    if part.startswith(("утр", "ноч")) and hour == HALF_DAY_HOURS:
        return 0
    if part.startswith("утр") or (part.startswith("ноч") and hour <= LATEST_NIGHT_HOUR):
        return hour
    return hour if hour == HALF_DAY_HOURS else hour + HALF_DAY_HOURS


# Примечание: «ср 28 октября» или «чт 3 июня 2027» (год — если не текущий)
def humanDate(day: date, today: date | None = None) -> str:
    label = f"{WEEKDAY_NAMES[day.weekday()]} {day.day} {MONTH_NAMES[day.month - 1]}"
    if today is not None and day.year != today.year:
        label += f" {day.year}"
    return label


def monthLabel(month: int) -> str:
    return next(name for name, number in _MONTHS_PREPOSITIONAL.items() if number == month)


def _weekdayLabel(weekday: int) -> str:
    names = ("в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу")
    return names[weekday] if weekday < len(names) else "в воскресенье"


# Примечание: несколько разных дней во фразе — спросить, какой
def _dateConflict(days: list[date]) -> Problem:
    labels = [humanDate(day) for day in days[:3]]
    return Problem(f"Уточни день: {' или '.join(labels)}?", labels)
