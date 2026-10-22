"""Периоды «сегодня / завтра / неделя»: границы в поясе пользователя, неделя с понедельника."""

from datetime import datetime

import pytest

from agent.domain.periods import Period, isPeriod, periodRange

MSK = "Europe/Moscow"


# Примечание: один и тот же момент UTC — разные «сегодня» в Москве и Екатеринбурге
def test_sameInstantDifferentToday() -> None:
    instant = datetime.fromisoformat("2026-10-21T20:30:00+00:00")
    msk = periodRange("today", instant, MSK)
    ekb = periodRange("today", instant, "Asia/Yekaterinburg")
    assert msk.start.date().isoformat() == "2026-10-21"
    assert ekb.start.date().isoformat() == "2026-10-22"


# Примечание: края: полночь, воскресенье → неделя с прошлого понедельника, конец не включается
@pytest.mark.parametrize(
    ("period", "now", "start", "end"),
    [
        (
            "today",
            "2026-10-21T00:00:00+03:00",
            "2026-10-21T00:00:00+03:00",
            "2026-10-22T00:00:00+03:00",
        ),
        (
            "tomorrow",
            "2026-10-21T23:59:00+03:00",
            "2026-10-22T00:00:00+03:00",
            "2026-10-23T00:00:00+03:00",
        ),
        (
            "week",
            "2026-10-25T23:00:00+03:00",
            "2026-10-19T00:00:00+03:00",
            "2026-10-26T00:00:00+03:00",
        ),
        (
            "week",
            "2026-10-26T00:00:00+03:00",
            "2026-10-26T00:00:00+03:00",
            "2026-11-02T00:00:00+03:00",
        ),
        (
            "week",
            "2026-12-30T12:00:00+03:00",
            "2026-12-28T00:00:00+03:00",
            "2027-01-04T00:00:00+03:00",
        ),
    ],
)
def test_edges(period: Period, now: str, start: str, end: str) -> None:
    span = periodRange(period, datetime.fromisoformat(now), MSK)
    assert (span.start.isoformat(), span.end.isoformat()) == (start, end)


# Примечание: переход на зимнее время внутри недели — у концов разные смещения
def test_weekAcrossDst() -> None:
    span = periodRange("week", datetime.fromisoformat("2026-10-21T12:00:00+02:00"), "Europe/Berlin")
    assert (span.start.isoformat(), span.end.isoformat()) == (
        "2026-10-19T00:00:00+02:00",
        "2026-10-26T00:00:00+01:00",
    )


# Примечание: из args кнопки принимаются только периоды каталога
def test_isPeriod() -> None:
    assert [isPeriod(v) for v in ("today", "tomorrow", "week", "month", 1, None)] == [
        True,
        True,
        True,
        False,
        False,
        False,
    ]
