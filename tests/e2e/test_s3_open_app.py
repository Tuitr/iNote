"""E2E S3 на стенде: кнопки мини-приложения J6 (шаг 4) — start_param по экрану и поясу."""

from datetime import datetime, timedelta

import httpx
import pytest
from tests.e2e.stand import USER_TZ, Chat, buttons, messagesTo, push

pytestmark = pytest.mark.e2e


# Примечание: (подпись, start_param) кнопок open_app сообщения
def apps(message: dict[str, object]) -> list[tuple[str, str]]:
    return [
        (b["text"], b["payload"])
        for row in buttons(message)
        for b in row
        if b["type"] == "open_app"
    ]


# Примечание: /start — «Мой день» (today) и «История» (dialog) в одном ряду
async def test_startButtons(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await push(bot, chat.text("/start"))
    [reply] = await messagesTo(bot, chat, 1)
    assert apps(reply) == [("Мой день", "today"), ("История", "dialog")]


# Примечание: сегодня — today, завтра — day_<дата>, неделя — week_<понедельник> в поясе
async def test_periodButtons(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    today = datetime.now(tz=USER_TZ).date()
    monday = today - timedelta(days=today.weekday())
    for command in ("/today", "/tomorrow", "/week"):
        await push(bot, chat.text(command))
    replies = await messagesTo(bot, chat, 3)
    assert [apps(r)[-1][1] for r in replies] == [
        "today",
        f"day_{today + timedelta(days=1)}",
        f"week_{monday}",
    ]
