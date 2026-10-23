"""Тексты уведомлений J4: пояс пользователя, края дат, экранирование, лимит 4000."""

from datetime import UTC, datetime

import pytest

from bot.presentation.templates import RussianNotificationTexts
from services.bot.tests.fakes import notifyRequest


# Примечание: текст напоминания на момент now в поясе tz
def body(now: datetime, tz: str, remindAt: str, title: str = "Позвонить Ивану") -> str:
    request = notifyRequest(
        user={"max_user_id": 1, "max_chat_id": 2, "tz": tz},
        item={"title": title, "remind_at": remindAt, "starts_at": None},
    )
    return RussianNotificationTexts(lambda: now).notificationBody(request)


# Примечание: края: 23:30 → «Завтра», конец года — год у чужого года, один момент в двух поясах
@pytest.mark.parametrize(
    ("now", "tz", "remindAt", "expected"),
    [
        (
            datetime(2026, 10, 21, 20, 30, tzinfo=UTC),
            "Europe/Moscow",
            "2026-10-22T09:00:00+03:00",
            "Завтра, чт 22 октября, 09:00",
        ),
        (
            datetime(2026, 12, 31, 20, 30, tzinfo=UTC),
            "Europe/Moscow",
            "2027-01-01T10:00:00+03:00",
            "Завтра, пт 1 января 2027, 10:00",
        ),
        (
            datetime(2026, 10, 25, 18, 0, tzinfo=UTC),
            "Asia/Yekaterinburg",
            "2026-10-25T20:00:00+00:00",
            "Завтра, пн 26 октября, 01:00",
        ),
        (
            datetime(2026, 10, 25, 18, 0, tzinfo=UTC),
            "Europe/Moscow",
            "2026-10-25T20:00:00+00:00",
            "Сегодня, вс 25 октября, 23:00",
        ),
    ],
)
def test_whenInUserTz(now: datetime, tz: str, remindAt: str, expected: str) -> None:
    assert body(now, tz, remindAt).split("\n")[1] == expected


# Примечание: название экранируется под markdown, длинное — в лимит MAX
def test_escapedAndLimited() -> None:
    now = datetime(2026, 10, 21, 6, 0, tzinfo=UTC)
    assert body(now, "Europe/Moscow", "2026-10-21T10:00:00+03:00", "*Важно* [x]").startswith(
        "⏰ \\*Важно\\* \\[x\\]\n"
    )
    long = body(now, "Europe/Moscow", "2026-10-21T10:00:00+03:00", "я" * 200)
    assert len(long) <= 4000
