"""«Отложить»: на час — от «сейчас», «Завтра» — то же время завтра; края дат и два пояса."""

from datetime import datetime

import pytest

from agent.application.snooze import snoozeFor, snoozeTomorrow

MSK = "Europe/Moscow"
EKB = "Asia/Yekaterinburg"


# Примечание: на час: через полночь, вечер воскресенья, конец месяца и года, другой пояс;
# секунды отбрасываются
@pytest.mark.parametrize(
    ("now", "tz", "expected"),
    [
        ("2026-10-21T23:30:45+03:00", MSK, "2026-10-22T00:30:00+03:00"),
        ("2026-10-25T23:10:00+03:00", MSK, "2026-10-26T00:10:00+03:00"),
        ("2026-10-31T23:30:00+03:00", MSK, "2026-11-01T00:30:00+03:00"),
        ("2026-12-31T23:59:59+03:00", MSK, "2027-01-01T00:59:00+03:00"),
        ("2026-10-21T20:30:00+00:00", EKB, "2026-10-22T02:30:00+05:00"),
    ],
)
def test_snoozeHour(now: str, tz: str, expected: str) -> None:
    assert snoozeFor(datetime.fromisoformat(now), tz, 60).isoformat() == expected


# Примечание: «Завтра»: время напоминания, если известно, иначе «сейчас»; завтра — от «сейчас»
# пользователя (в 23:30 по Москве «завтра» — следующий день Москвы, не UTC)
@pytest.mark.parametrize(
    ("now", "tz", "remindAt", "expected"),
    [
        (
            "2026-10-21T10:01:00+03:00",
            MSK,
            "2026-10-21T10:00:00+03:00",
            "2026-10-22T10:00:00+03:00",
        ),
        ("2026-10-25T21:40:00+03:00", MSK, None, "2026-10-26T21:40:00+03:00"),
        (
            "2026-10-31T23:30:00+03:00",
            MSK,
            "2026-10-31T23:30:00+03:00",
            "2026-11-01T23:30:00+03:00",
        ),
        ("2026-12-31T20:30:00+00:00", MSK, None, "2027-01-01T23:30:00+03:00"),
        (
            "2026-10-21T20:30:00+00:00",
            EKB,
            "2026-10-22T01:00:00+05:00",
            "2026-10-23T01:00:00+05:00",
        ),
    ],
)
def test_snoozeTomorrow(now: str, tz: str, remindAt: str | None, expected: str) -> None:
    moment = snoozeTomorrow(
        datetime.fromisoformat(now), tz, datetime.fromisoformat(remindAt) if remindAt else None
    )
    assert moment.isoformat() == expected


# Примечание: переход на зимнее время — завтра в те же стенные 10:00, смещение другое
def test_tomorrowAcrossDst() -> None:
    now = datetime.fromisoformat("2026-10-24T10:00:00+02:00")
    assert snoozeTomorrow(now, "Europe/Berlin", None).isoformat() == "2026-10-25T10:00:00+01:00"
