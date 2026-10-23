"""E2E S3 на стенде: /settings → пояс кнопкой → «сегодня» в новом поясе (шаг 5).

Путь: фейковый MAX → бот → агент (set_tz) → J8 PATCH в заглушке → сброс кеша пользователя →
следующий ход берёт пояс из resolve.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from tests.e2e.stand import Chat, buttons, messagesTo, payloadOf, push

from common.ru_dates import RU_DATE_WORDS

pytestmark = pytest.mark.e2e

KAMCHATKA = ZoneInfo("Asia/Kamchatka")


# Примечание: /settings → «Камчатка» → «Пояс: Камчатка (UTC+12)» → «что у меня сегодня» —
# заголовок с датой Камчатки; повторный /settings отмечает новый пояс
async def test_setTzThenToday(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await push(bot, chat.text("/settings"))
    [screen] = await messagesTo(bot, chat, 1)
    assert "Пояс: Москва (UTC+3)" in str(screen["text"])
    assert max(len(row) for row in buttons(screen)) <= 7
    await push(bot, chat.press(payloadOf(screen, "Камчатка")))
    _, changed = await messagesTo(bot, chat, 2)
    assert changed["text"] == "Пояс: Камчатка (UTC+12)"
    await push(bot, chat.text("что у меня сегодня"))
    *_, today = await messagesTo(bot, chat, 3)
    day = datetime.now(tz=KAMCHATKA).date()
    header = str(today["text"]).split("\n", 1)[0]
    assert header.startswith("Сегодня, "), header
    assert f" {day.day} {RU_DATE_WORDS.months[day.month - 1]}" in header, header
    await push(bot, chat.text("/settings"))
    *_, again = await messagesTo(bot, chat, 4)
    assert "Пояс: Камчатка (UTC+12)" in str(again["text"])
    assert "✓ Камчатка" in [b["text"] for row in buttons(again) for b in row]
