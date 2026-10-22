"""E2E S1 на стенде: фейковый MAX → бот → агент → ScriptedBrain → MCP-заглушка → шаблон → бот.

Запуск: `make e2e` (поднимает стенд) или `uv run pytest -m e2e` при уже поднятом стенде.
"""

import asyncio
import uuid
from datetime import datetime, timedelta

import httpx
import pytest
from tests.e2e.stand import USER_TZ, Chat, buttons, messagesTo, push

pytestmark = pytest.mark.e2e


# Примечание: главный сценарий S1 — «Что у меня сегодня?» → список по шаблону с кнопками
async def test_whatIsToday(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await push(bot, chat.text("Что у меня сегодня?"))
    [reply] = await messagesTo(bot, chat, 1)
    text = str(reply["text"])
    today = datetime.now(tz=USER_TZ).date()
    assert text.startswith("Сегодня, "), text
    assert f" {today.day} " in text.split("\n", 1)[0]
    assert reply["format"] == "markdown"
    rows = buttons(reply)
    assert [b["text"] for b in rows[0]] == ["Завтра", "Неделя"]
    assert all(b["type"] == "callback" for b in rows[0])
    [app] = rows[1]
    assert app["type"] in {"open_app", "link"}
    assert f"day_{today.isoformat()}" in (app.get("payload") or app.get("url") or "")


# Примечание: «напомни завтра в 10» → подтверждение из сохранённого; «Отменить» → /v1/actions
async def test_reminderAndButton(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await push(bot, chat.text("Напомни завтра в 10 позвонить Ивану"))
    [reply] = await messagesTo(bot, chat, 1)
    tomorrow = datetime.now(tz=USER_TZ).date() + timedelta(days=1)
    lines = str(reply["text"]).split("\n")
    assert lines[0] == "Напоминание создано"
    assert lines[1] == "Позвонить Ивану"
    assert lines[2].startswith("Завтра, ")
    assert lines[2].endswith(", 10:00")
    assert f" {tomorrow.day} " in lines[2]
    undo = buttons(reply)[0][0]
    assert (undo["type"], undo["text"]) == ("callback", "Отменить")
    await push(bot, chat.press(undo["payload"]))
    # кнопка → ключ в Redis → /v1/actions → delete_item → подтверждение из данных
    _, answer = await messagesTo(bot, chat, 2)
    assert str(answer["text"]).startswith("Отменено\nПозвонить Ивану")


# Примечание: один апдейт дважды — один ответ (дедуп в Redis)
async def test_duplicateUpdate(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    raw = chat.text("Что у меня завтра?", mid=f"mid.e2e.dup.{uuid.uuid4().hex}")
    await push(bot, raw)
    await push(bot, raw)
    [reply] = await messagesTo(bot, chat, 1)
    assert str(reply["text"]).startswith("Завтра, ")
    await asyncio.sleep(1)
    assert len(await messagesTo(bot, chat, 1)) == 1


# Примечание: фраза вне правил ScriptedBrain — подсказка, а не ошибка
async def test_unknownPhrase(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await push(bot, chat.text("Расскажи анекдот"))
    [reply] = await messagesTo(bot, chat, 1)
    assert str(reply["text"]).startswith("Пока понимаю только")
    assert reply["format"] == "plain"
