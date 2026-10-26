"""Разбор даты и времени кодом: примеры из отчёта о тестах 26.10.2026 и похожие формулировки."""

from datetime import date, datetime, timedelta

import pytest

from brain.spoken_time import SaidTime, Spoken, normalize, parseSpoken, remindOffset, withoutTime

MSK = "Europe/Moscow"
# понедельник 26.10.2026, ночь — когда шли тесты
NIGHT = datetime.fromisoformat("2026-10-26T01:46:00+03:00")
DAY = datetime.fromisoformat("2026-10-26T14:00:00+03:00")


def parse(text: str, now: datetime = NIGHT) -> Spoken:
    return parseSpoken(text, now, MSK)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("на десять пятнадцать", "на 10 15"),
        ("без десяти три", "без десяти 3"),
        ("в двадцать три тридцать", "в 23 30"),
        ("Ещё, раз-два", "еще раз 2"),
    ],
)
def test_normalize(text: str, expected: str) -> None:
    assert normalize(text) == expected


@pytest.mark.parametrize(
    ("text", "said"),
    [
        # час 1–12 без части суток — неясно
        ("на десять пятнадцать", SaidTime(10, 15, isClear=False)),
        ("Давай без десяти 3", SaidTime(2, 50, isClear=False)),
        ("без четверти девять", SaidTime(8, 45, isClear=False)),
        ("в 5", SaidTime(5, 0, isClear=False)),
        ("в 12", SaidTime(0, 0, isClear=False)),
        ("пол первого", SaidTime(0, 30, isClear=False)),
        ("в 10:15", SaidTime(10, 15, isClear=False)),
        # часть суток или формат — ясно
        ("Без десяти три дня, исправь", SaidTime(14, 50, isClear=True)),
        ("в 5 вечера", SaidTime(17, 0, isClear=True)),
        ("в 9 утра", SaidTime(9, 0, isClear=True)),
        ("в 3 часа дня", SaidTime(15, 0, isClear=True)),
        ("в 2 ночи", SaidTime(2, 0, isClear=True)),
        ("в 11 ночи", SaidTime(23, 0, isClear=True)),
        ("в 12 ночи", SaidTime(0, 0, isClear=True)),
        ("вечером в 7", SaidTime(19, 0, isClear=True)),
        ("в 15:00", SaidTime(15, 0, isClear=True)),
        ("в 21:30", SaidTime(21, 30, isClear=True)),
        ("в 09:30", SaidTime(9, 30, isClear=True)),
        ("напомни в обед", SaidTime(13, 0, isClear=True)),
        ("в полночь", SaidTime(0, 0, isClear=True)),
        ("пол третьего дня", SaidTime(14, 30, isClear=True)),
    ],
)
def test_time(text: str, said: SaidTime) -> None:
    assert parse(text).time == said


@pytest.mark.parametrize(
    ("text", "now", "day"),
    [
        # «завтра» и «следующий день» — следующая дата, и ночью тоже
        ("Поставь ближе к 12 ночи следующего дня напоминание", NIGHT, date(2026, 10, 27)),
        ("завтра в 9 утра", NIGHT, date(2026, 10, 27)),
        ("после завтра в 18 00", DAY, date(2026, 10, 28)),
        ("завтра в 9 утра", DAY, date(2026, 10, 27)),
        ("послезавтра", DAY, date(2026, 10, 28)),
        ("через неделю", DAY, date(2026, 11, 2)),
        ("через 3 дня", DAY, date(2026, 10, 29)),
        ("через 2 недели", DAY, date(2026, 11, 9)),
        ("в пятницу", DAY, date(2026, 10, 30)),
        ("в понедельник", DAY, date(2026, 10, 26)),
        # дата без года — ближайшая будущая
        ("Запланируй 1 мая поездку на шашлыки", DAY, date(2027, 5, 1)),
        ("30 октября", DAY, date(2026, 10, 30)),
        ("03.06.2027 в 10:00", DAY, date(2027, 6, 3)),
        ("15.11", DAY, date(2026, 11, 15)),
        ("29 февраля", DAY, date(2028, 2, 29)),
        ("в мае 3 мая", DAY, date(2027, 5, 3)),
    ],
)
def test_day(text: str, now: datetime, day: date) -> None:
    spoken = parse(text, now)
    assert (spoken.problem, spoken.day) == (None, day)


@pytest.mark.parametrize(
    ("text", "question"),
    [
        ("Мне нужно 01.13 съездить домой", "Такой даты нет: «01.13». Какую дату ты имел в виду?"),
        ("31.02 оплатить", "Такой даты нет: «31.02». Какую дату ты имел в виду?"),
        ("32.01", "Такой даты нет: «32.01». Какую дату ты имел в виду?"),
        ("31 апреля", "Такой даты нет: «31 апреля». Какую дату ты имел в виду?"),
        ("поездка на шашлыки в мае 03.06.2027 в 10:00", "Уточни дату: в мае или чт 3 июня?"),
        ("в пятницу 28.10", "Уточни день: в пятницу или ср 28 октября?"),
        ("завтра 30.10", "Уточни день: вт 27 октября или пт 30 октября?"),
        ("03.05.2026 в 10:00", "Дата 03.05.2026 уже прошла. На какой день поставить?"),
    ],
)
def test_problem(text: str, question: str) -> None:
    spoken = parseSpoken(text, DAY, MSK)
    assert spoken.problem is not None
    assert spoken.problem.question == question


@pytest.mark.parametrize(
    "text",
    ["через 2 часа", "через полчаса в 5", "на следующей неделе", "в выходные"],
)
# Примечание: сложнее правил — разбирает модель
def test_leftToModel(text: str) -> None:
    assert parse(text).supported is False


@pytest.mark.parametrize(
    "text",
    ["Запланируй встречу с зубмастером", "напомни купить хлеб", "в 3 раза больше", "на 5 минут"],
)
# Примечание: ни дня, ни времени
def test_nothing(text: str) -> None:
    spoken = parse(text)
    assert (spoken.day, spoken.time, spoken.problem) == (None, None, None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Задача купить билеты завтра в 5", "задача купить билеты завтра"),
        ("Добавь задачу сдать отчёт послезавтра в 15:00", "добавь задачу сдать отчет послезавтра"),
        ("задача в обед купить хлеб", "задача купить хлеб"),
    ],
)
# Примечание: задача — модели без времени: её срок — дата на весь день
def test_withoutTime(text: str, expected: str) -> None:
    assert withoutTime(text) == expected


# Смещение напоминания «за N …» — от начала записи; без «за» — нет
def test_remindOffset() -> None:
    assert remindOffset("напомни об этой встрече за 40 минут") == timedelta(minutes=40)
    assert remindOffset("напомни за сорок минут") == timedelta(minutes=40)
    assert remindOffset("за час до встречи") == timedelta(hours=1)
    assert remindOffset("напомни за полчаса") == timedelta(minutes=30)
    assert remindOffset("за 2 часа напомни") == timedelta(hours=2)
    assert remindOffset("напомни за день") == timedelta(days=1)
    assert remindOffset("напомни завтра в 10") is None


# «Завтра ближе к полночь» (так распознаёт речь) — полночь в конце завтрашнего дня, а не 00:00
# в начале завтра, то есть этой ночью
@pytest.mark.parametrize(
    "text",
    [
        "завтра ближе к полночь напомни почистить зубы",
        "завтра в полночь",
        "завтра ближе к полуночи",
    ],
)
def test_midnightIsEndOfNamedDay(text: str) -> None:
    spoken = parse(text)
    assert (spoken.day, spoken.time) == (
        parse("послезавтра").day,
        SaidTime(0, 0, isClear=True),
    )
