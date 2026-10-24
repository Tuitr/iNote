"""E2E S3 на стенде: сквозные сценарии шага 7 (уведомления, кнопки, фокус, конфликт, пояс).

Scheduler заменяет вызов J4 из теста (как notify.sh). Голосового ответа ассистента нет — сценария 8
нет (решение команды). Запуск: `make e2e` или `uv run pytest -m e2e` при поднятом стенде.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from tests.e2e.stand import (
    USER_TZ,
    Chat,
    buttons,
    given,
    itemIdOf,
    labels,
    messagesTo,
    notifyRaw,
    payloadOf,
    push,
    voice,
)

from common.ru_dates import RU_DATE_WORDS

pytestmark = pytest.mark.e2e

FOCUS_WAIT_S = 0.5
EKB = ZoneInfo("Asia/Yekaterinburg")


# Примечание: «сейчас» пользователя до минуты + minutes — время напоминания «через …»
def inMinutes(minutes: int) -> datetime:
    now = datetime.now(tz=USER_TZ).replace(second=0, microsecond=0)
    return now + timedelta(minutes=minutes)


# Примечание: «Напомни через минуту позвонить Ивану» голосом → создано; id — из кнопки записи
async def createInMinute(bot: httpx.AsyncClient, chat: Chat) -> str:
    await voice(bot, chat, "Напомни через минуту позвонить Ивану")
    [created] = await messagesTo(bot, chat, 1)
    lines = str(created["text"]).split("\n")
    assert lines[1:3] == ["Напоминание создано", "Позвонить Ивану"], lines
    return itemIdOf(created)


# Примечание: 1 и 2 — уведомление: три кнопки и «В календаре»; тот же notification_id повтором и
# параллельно — одно сообщение, повтор — duplicate с тем же message_id
async def test_notificationOnceWithButtons(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    itemId = await createInMinute(bot, chat)
    nid = str(uuid.uuid4())
    first, second = await asyncio.gather(
        notifyRaw(bot, chat, itemId, "Позвонить Ивану", inMinutes(1), notificationId=nid),
        notifyRaw(bot, chat, itemId, "Позвонить Ивану", inMinutes(1), notificationId=nid),
    )
    assert {first.status_code, second.status_code} <= {200, 503}
    retry = await notifyRaw(
        bot, chat, itemId, "Позвонить Ивану", inMinutes(1), notificationId=nid, attempt=3
    )
    assert retry.status_code == 200, retry.text
    assert retry.json()["duplicate"] is True
    _, notice = await messagesTo(bot, chat, 2)
    await asyncio.sleep(FOCUS_WAIT_S)
    assert len(await messagesTo(bot, chat, 2)) == 2  # второго уведомления нет
    assert retry.json()["message_id"] == notice["message_id"]
    assert str(notice["text"]).startswith("⏰ Позвонить Ивану\n")
    rows = buttons(notice)
    assert [b["text"] for b in rows[0]] == ["Выполнено", "Отложить на 1 ч", "Завтра"]
    assert all(b["type"] == "callback" for b in rows[0])
    assert [(b["type"], b["payload"]) for b in rows[1]] == [("open_app", f"item_{itemId}")]


# Примечание: 3 — «Отложить на 1 ч» → время напоминания +1 ч от «сейчас»; «Выполнено» →
# выполнено; повтор «Выполнено» → «Уже отмечено выполненным»
async def test_snoozeThenComplete(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    itemId = await createInMinute(bot, chat)
    response = await notifyRaw(bot, chat, itemId, "Позвонить Ивану", inMinutes(1))
    assert response.status_code == 200, response.text
    _, notice = await messagesTo(bot, chat, 2)
    await push(bot, chat.press(payloadOf(notice, "Отложить на 1 ч")))
    *_, snoozed = await messagesTo(bot, chat, 3)
    first = str(snoozed["text"]).split("\n")[0]
    expected = {f"{m:%H:%M}" for m in (inMinutes(60), inMinutes(59), inMinutes(61))}
    assert first.startswith("Напомню: "), first
    assert first.rsplit(", ", 1)[-1] in expected, first
    await push(bot, chat.press(payloadOf(notice, "Выполнено")))
    *_, done = await messagesTo(bot, chat, 4)
    assert str(done["text"]).startswith("Отмечено выполненным\nПозвонить Ивану")
    await push(bot, chat.press(payloadOf(notice, "Выполнено")))
    *_, again = await messagesTo(bot, chat, 5)
    assert again["text"] == "Уже отмечено выполненным."


# Примечание: 4 — после уведомления «перенеси его на завтра в 15» голосом → изменена эта запись
async def test_moveItByVoiceAfterNotification(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    itemId = await createInMinute(bot, chat)
    response = await notifyRaw(bot, chat, itemId, "Позвонить Ивану", inMinutes(1))
    assert response.status_code == 200, response.text
    await asyncio.sleep(FOCUS_WAIT_S)  # фокус бот ставит фоном после ответа J4
    await voice(bot, chat, "перенеси его на завтра в 15")
    *_, moved = await messagesTo(bot, chat, 3)
    lines = str(moved["text"]).split("\n")
    assert lines[0] == "Распознал: «перенеси его на завтра в 15»", lines
    assert lines[1:3] == ["Изменено", "Позвонить Ивану"], lines
    assert lines[3].startswith("Завтра, "), lines
    assert lines[3].endswith(", 15:00"), lines
    assert itemIdOf(moved) == itemId


# Примечание: 5 — пересечение встреч → «Всё равно создать» → встреча создана в то же время
async def test_conflictForceCreate(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await given(bot, chat, "Встреча со стоматологом завтра в 9:30")
    await voice(bot, chat, "Встреча с Петром завтра в 9:30")
    _, asked = await messagesTo(bot, chat, 2)
    assert "Всё равно создать" in labels(asked), labels(asked)
    await push(bot, chat.press(payloadOf(asked, "Всё равно создать")))
    *_, created = await messagesTo(bot, chat, 3)
    lines = str(created["text"]).split("\n")
    assert lines[:2] == ["Встреча создана", "Встреча с Петром"], lines
    assert lines[2].startswith("Завтра, "), lines
    assert lines[2].endswith(", 09:30"), lines


# Примечание: 6 — /settings → «Екатеринбург» → «что у меня сегодня» — день Екатеринбурга
async def test_settingsEkaterinburg(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await push(bot, chat.text("/settings"))
    [screen] = await messagesTo(bot, chat, 1)
    assert "олосов" not in str(screen["text"])
    await push(bot, chat.press(payloadOf(screen, "Екатеринбург")))
    _, changed = await messagesTo(bot, chat, 2)
    assert changed["text"] == "Пояс: Екатеринбург (UTC+5)"
    await push(bot, chat.text("что у меня сегодня"))
    *_, today = await messagesTo(bot, chat, 3)
    day = datetime.now(tz=EKB).date()
    header = str(today["text"]).split("\n", 1)[0]
    assert header.startswith(f"Сегодня, {RU_DATE_WORDS.weekdays[day.weekday()]} {day.day} "), header


# Примечание: 7 — чат «заблокирован» → 404 chat_not_found, сообщений нет
async def test_chatNotFound(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    plan = {"chat_id": chat.chatId, "reason": "chat_not_found", "times": 1}
    assert (await bot.post("/dev/max/failures", json=plan)).status_code == 200
    item = str(uuid.uuid4())
    response = await notifyRaw(bot, chat, item, "Позвонить Ивану", inMinutes(1))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "chat_not_found"
    sent = (await bot.get("/dev/max/sent")).json()["messages"]
    assert [m for m in sent if m["chat_id"] == chat.chatId] == []
