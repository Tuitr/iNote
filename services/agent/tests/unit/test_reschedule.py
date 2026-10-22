"""Варианты «Изменить время»: от «сейчас» и времени записи, без прошлого и без повторов."""

from datetime import datetime

import pytest

from agent.application.reschedule import RescheduleChoice, rescheduleOptions

MSK = "Europe/Moscow"


# Примечание: (вид, время) вариантов для «сейчас» и начала записи
def options(now: str, item: str | None) -> list[tuple[str, str]]:
    start = datetime.fromisoformat(item) if item else None
    return [
        (o.choice.value, o.startsAt.isoformat())
        for o in rescheduleOptions(datetime.fromisoformat(now), MSK, start)
    ]


# Примечание: края: запись сегодня, запись на завтра (её же время не предлагаем), время
# совпало с 9:00, записи нет в фокусе — «это же время» от «сейчас»; секунды отбрасываются
@pytest.mark.parametrize(
    ("now", "item", "expected"),
    [
        (
            "2026-10-21T21:30:00+03:00",
            "2026-10-21T23:00:00+03:00",
            [
                ("in_hour", "2026-10-21T22:30:00+03:00"),
                ("same_time_tomorrow", "2026-10-22T23:00:00+03:00"),
                ("tomorrow_morning", "2026-10-22T09:00:00+03:00"),
            ],
        ),
        (
            "2026-10-21T21:30:00+03:00",
            "2026-10-22T15:00:00+03:00",
            [
                ("in_hour", "2026-10-21T22:30:00+03:00"),
                ("tomorrow_morning", "2026-10-22T09:00:00+03:00"),
            ],
        ),
        (
            "2026-10-21T21:30:00+03:00",
            "2026-10-21T09:00:00+03:00",
            [
                ("in_hour", "2026-10-21T22:30:00+03:00"),
                ("same_time_tomorrow", "2026-10-22T09:00:00+03:00"),
            ],
        ),
        (
            "2026-10-21T18:30:45+00:00",
            None,
            [
                ("in_hour", "2026-10-21T22:30:00+03:00"),
                ("same_time_tomorrow", "2026-10-22T21:30:00+03:00"),
                ("tomorrow_morning", "2026-10-22T09:00:00+03:00"),
            ],
        ),
    ],
)
def test_options(now: str, item: str | None, expected: list[tuple[str, str]]) -> None:
    assert options(now, item) == expected


# Примечание: вид варианта — только из перечня (подписи берёт renderer)
def test_choices() -> None:
    assert {c.value for c in RescheduleChoice} == {
        "in_hour",
        "same_time_tomorrow",
        "tomorrow_morning",
    }
