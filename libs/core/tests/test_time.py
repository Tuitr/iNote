from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest

from libs.core.services.time import (
    days_range_utc,
    local_midnight_utc,
    local_time_utc,
    now_utc,
    to_user_tz,
    to_utc,
)

MOSCOW = "Europe/Moscow"
YEKATERINBURG = "Asia/Yekaterinburg"
BERLIN = "Europe/Berlin"


def test_now_is_aware_utc() -> None:
    assert now_utc().utcoffset() == timedelta(0)


def test_to_utc_keeps_the_moment() -> None:
    moscow_ten = datetime(2026, 10, 22, 10, 0, tzinfo=timezone(timedelta(hours=3)))

    assert to_utc(moscow_ten) == datetime(2026, 10, 22, 7, 0, tzinfo=UTC)
    assert to_utc(moscow_ten).tzinfo is UTC


def test_to_user_tz_shows_local_clock() -> None:
    seven_utc = datetime(2026, 10, 22, 7, 0, tzinfo=UTC)

    assert to_user_tz(seven_utc, MOSCOW).hour == 10
    assert to_user_tz(seven_utc, YEKATERINBURG).hour == 12
    assert to_user_tz(seven_utc, MOSCOW).isoformat() == "2026-10-22T10:00:00+03:00"


@pytest.mark.parametrize("function", [to_utc, lambda value: to_user_tz(value, MOSCOW)])
def test_naive_time_is_rejected(function: Callable[[datetime], datetime]) -> None:
    with pytest.raises(ValueError, match="без часового пояса"):
        function(datetime(2026, 10, 22, 10, 0))


def test_tomorrow_starts_at_different_utc_moments_in_moscow_and_yekaterinburg() -> None:
    tomorrow = date(2026, 10, 22)

    assert local_midnight_utc(tomorrow, MOSCOW) == datetime(2026, 10, 21, 21, 0, tzinfo=UTC)
    assert local_midnight_utc(tomorrow, YEKATERINBURG) == datetime(2026, 10, 21, 19, 0, tzinfo=UTC)


def test_day_range_is_half_open_in_user_tz() -> None:
    start, end = days_range_utc(date(2026, 10, 22), date(2026, 10, 23), MOSCOW)

    assert start == datetime(2026, 10, 21, 21, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 22, 21, 0, tzinfo=UTC)


def test_day_with_dst_switch_is_23_hours_long() -> None:
    # В Берлине 29 марта 2026 стрелки переводят вперёд: в этих сутках 23 часа.
    start, end = days_range_utc(date(2026, 3, 29), date(2026, 3, 30), BERLIN)

    assert end - start == timedelta(hours=23)


def test_local_offset_follows_dst() -> None:
    noon_utc_in_summer = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    noon_utc_in_winter = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)

    assert to_user_tz(noon_utc_in_summer, BERLIN).isoformat() == "2026-07-01T14:00:00+02:00"
    assert to_user_tz(noon_utc_in_winter, BERLIN).isoformat() == "2026-12-01T13:00:00+01:00"


def test_day_without_midnight_starts_at_the_switch() -> None:
    # В Сантьяго 6 сентября 2026 после 23:59:59 сразу наступает 01:00: полуночи нет,
    # день начинается в 01:00 по местному времени.
    start = local_midnight_utc(date(2026, 9, 6), "America/Santiago")

    assert start == datetime(2026, 9, 6, 4, 0, tzinfo=UTC)
    assert to_user_tz(start, "America/Santiago").isoformat() == "2026-09-06T01:00:00-03:00"


def test_local_time_is_converted_to_utc() -> None:
    assert local_time_utc(date(2026, 10, 22), time(8, 0), MOSCOW) == datetime(
        2026, 10, 22, 5, 0, tzinfo=UTC
    )
