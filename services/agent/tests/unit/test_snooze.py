"""«Отложить»: на час — от «сейчас»; края дат и два пояса."""

from datetime import datetime

import pytest

from agent.application.snooze import snoozeFor

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
