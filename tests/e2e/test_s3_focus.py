"""E2E S3 на стенде: фокус после уведомления и «перенеси его…» (шаг 3).

Уведомление J4 (за Scheduler) → бот доставил → /v1/focus агента → фраза без названия записи
меняет именно эту запись. Без фокуса и при нескольких записях — уточнение.
Запуск: `make e2e` или `uv run pytest -m e2e` при поднятом стенде.
"""

import asyncio
from datetime import datetime, timedelta

import httpx
import pytest
from tests.e2e.stand import (
    USER_TZ,
    Chat,
    itemIdOf,
    labels,
    messagesTo,
    notify,
    payloadOf,
    push,
)

pytestmark = pytest.mark.e2e

FOCUS_WAIT_S = 0.5


# Примечание: завтра 10:00 в поясе пользователя — время напоминания из фразы
def tomorrowAt(hour: int) -> datetime:
    day = datetime.now(tz=USER_TZ).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, hour, tzinfo=USER_TZ)


# Примечание: напоминание → уведомление о нём → «перенеси его на завтра в 15»: двигается эта
# запись (её id в кнопке ответа), вопроса «какую?» нет
async def test_moveItAfterNotification(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await push(bot, chat.text("Напомни завтра в 10 позвонить Ивану"))
    [created] = await messagesTo(bot, chat, 1)
    itemId = itemIdOf(created)
    delivered = await notify(bot, chat, itemId, "Позвонить Ивану", tomorrowAt(10))
    assert (delivered["delivered"], delivered["duplicate"]) == (True, False)
    await asyncio.sleep(FOCUS_WAIT_S)  # /v1/focus бот шлёт фоном после ответа J4
    await push(bot, chat.text("перенеси его на завтра в 15"))
    *_, moved = await messagesTo(bot, chat, 3)
    lines = str(moved["text"]).split("\n")
    assert "Позвонить Ивану" in lines, lines
    assert any(line.endswith(", 15:00") for line in lines), lines
    assert itemIdOf(moved) == itemId


# Примечание: фокус пуст (новый чат) — «Какую запись перенести?» без вариантов
async def test_moveWithoutFocusAsks(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await push(bot, chat.text("перенеси её на завтра в 15"))
    [reply] = await messagesTo(bot, chat, 1)
    assert str(reply["text"]).startswith("Какую запись перенести?")


# Примечание: два напоминания и уведомление о первом: «отметь его выполненным» — про него
async def test_completeItAfterNotification(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await push(bot, chat.text("Напомни завтра в 10 полить цветы"))
    [created] = await messagesTo(bot, chat, 1)
    await push(bot, chat.text("Напомни завтра в 11 купить хлеб"))
    await messagesTo(bot, chat, 2)
    await notify(bot, chat, itemIdOf(created), "Полить цветы", tomorrowAt(10))
    await asyncio.sleep(FOCUS_WAIT_S)
    await push(bot, chat.text("отметь его выполненным"))
    *_, done = await messagesTo(bot, chat, 4)
    assert "Полить цветы" in str(done["text"]), done["text"]
    assert "Купить хлеб" not in str(done["text"])
    assert "Какую" not in str(done["text"])
    assert labels(done)


# Примечание: после списка из двух записей «перенеси на завтра в 15» — «Какую запись
# перенести?» с вариантами; кнопка второй переносит именно её
async def test_moveAfterListPicks(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await push(bot, chat.text("Напомни завтра в 10 полить цветы"))
    await messagesTo(bot, chat, 1)
    await push(bot, chat.text("Напомни завтра в 11 купить хлеб"))
    await messagesTo(bot, chat, 2)
    await push(bot, chat.text("что у меня завтра"))
    await messagesTo(bot, chat, 3)
    await push(bot, chat.text("перенеси на завтра в 15"))
    *_, asked = await messagesTo(bot, chat, 4)
    assert str(asked["text"]).startswith("Какую запись перенести?"), asked["text"]
    # в заглушке MCP есть и общие записи из fixtures — ищем свои по подписи
    assert "Полить цветы — завтра 10:00" in labels(asked), labels(asked)
    await push(bot, chat.press(payloadOf(asked, "Купить хлеб — завтра 11:00")))
    *_, moved = await messagesTo(bot, chat, 5)
    lines = str(moved["text"]).split("\n")
    assert "Купить хлеб" in lines, lines
    assert any(line.endswith(", 15:00") for line in lines), lines
