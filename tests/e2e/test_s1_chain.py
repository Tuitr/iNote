"""E2E S1 на стенде: фейковый MAX → бот → агент → ScriptedBrain → MCP-заглушка → шаблон → бот.

Запуск: `make e2e` (поднимает стенд) или `uv run pytest -m e2e` при уже поднятом стенде.
Каждый тест — свои max_user_id и чат: порядок запуска не важен, журнал фейкового MAX общий.
"""

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

import httpx
import pytest
import pytest_asyncio
from pydantic import TypeAdapter

pytestmark = pytest.mark.e2e

BOT_URL: Final = os.environ.get(
    "BOT_URL", f"http://127.0.0.1:{os.environ.get('BOT_DEV_PORT', '8001')}"
)
# Пояс пользователя по умолчанию в заглушке J8; «сегодня» считаем в нём, а не в поясе машины
USER_TZ: Final = ZoneInfo("Europe/Moscow")
WAIT_S: Final = 15.0

type Json = dict[str, object]

_MESSAGES: Final = TypeAdapter(list[dict[str, object]])
_BUTTONS: Final = TypeAdapter(list[list[dict[str, str]]])


# Примечание: свой пользователь и чат на тест
class Chat:
    # Примечание: id из uuid — не пересекаются между тестами и прогонами
    def __init__(self) -> None:
        seed = uuid.uuid4().int
        self.userId = 10**9 + seed % 10**9
        self.chatId = 2 * 10**9 + seed % 10**9

    # Примечание: апдейт message_created по схеме MAX (как fixtures/max_updates/text.json)
    def text(self, text: str, mid: str | None = None) -> Json:
        now = int(datetime.now(tz=USER_TZ).timestamp() * 1000)
        return {
            "update_type": "message_created",
            "timestamp": now,
            "message": {
                "sender": {"user_id": self.userId, "first_name": "E2E", "is_bot": False},
                "recipient": {"chat_id": self.chatId, "chat_type": "dialog"},
                "timestamp": now,
                "body": {"mid": mid or f"mid.e2e.{uuid.uuid4().hex}", "text": text},
            },
            "user_locale": "ru",
        }

    # Примечание: нажатие кнопки с payload из нашего же сообщения
    def press(self, payload: str) -> Json:
        now = int(datetime.now(tz=USER_TZ).timestamp() * 1000)
        user = {"user_id": self.userId, "first_name": "E2E", "is_bot": False}
        return {
            "update_type": "message_callback",
            "timestamp": now,
            "callback": {
                "timestamp": now,
                "callback_id": f"cb.e2e.{uuid.uuid4().hex}",
                "payload": payload,
                "user": user,
            },
            "message": {
                "recipient": {"chat_id": self.chatId, "chat_type": "dialog"},
                "timestamp": now,
                "body": {"mid": f"mid.e2e.bot.{uuid.uuid4().hex}", "text": "-"},
            },
        }


# Примечание: клиент к dev-роутам бота; стенд не поднят — понятная ошибка, а не таймаут
@pytest_asyncio.fixture
async def bot() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=BOT_URL, timeout=5) as client:
        try:
            ready = await client.get("/health/ready")
        except httpx.TransportError as exc:
            pytest.fail(f"стенд не поднят ({BOT_URL}): make up или make e2e — {exc}")
        assert ready.status_code == 200, ready.text
        yield client


# Примечание: подложить апдейт фейковому MAX
async def push(client: httpx.AsyncClient, raw: Json) -> None:
    response = await client.post("/dev/max/updates", json=raw)
    assert response.status_code == 200, response.text


# Примечание: дождаться count сообщений бота в этот чат
async def messagesTo(client: httpx.AsyncClient, chat: Chat, count: int) -> list[Json]:
    async with asyncio.timeout(WAIT_S):
        while True:
            sent = (await client.get("/dev/max/sent")).json()
            mine = [
                m
                for m in _MESSAGES.validate_python(sent["messages"])
                if m["chat_id"] == chat.chatId
            ]
            if len(mine) >= count:
                return mine
            await asyncio.sleep(0.1)


# Примечание: кнопки сообщения (вложение inline_keyboard) по рядам
def buttons(message: Json) -> list[list[dict[str, str]]]:
    attachments = _MESSAGES.validate_python(message["attachments"])
    if not attachments:
        return []
    payload = attachments[0]["payload"]
    assert isinstance(payload, dict)
    return _BUTTONS.validate_python(payload["buttons"])


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
    # /v1/actions до S2 — заглушка агента: важен сам путь кнопка → ключ в Redis → агент → ответ
    _, answer = await messagesTo(bot, chat, 2)
    assert answer["text"]


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
