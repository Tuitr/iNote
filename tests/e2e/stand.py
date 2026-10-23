"""Общее для e2e на стенде: свой пользователь и чат на тест, апдейты фейковому MAX, журнал ответов.

Каждый тест — свои max_user_id и чат: порядок запуска не важен, журнал фейкового MAX общий.
"""

import asyncio
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import httpx
from pydantic import TypeAdapter

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


# Примечание: голосовое через /dev/max/voice: фейковый MAX кладёт «OggS» + текст, ASR-заглушка
# возвращает этот текст; пустой текст — no_speech, «~low~ …» — неуверенное распознавание
async def voice(client: httpx.AsyncClient, chat: Chat, text: str) -> None:
    body = {"max_user_id": chat.userId, "max_chat_id": chat.chatId, "text": text}
    response = await client.post("/dev/max/voice", json=body)
    assert response.status_code == 200, response.text


# Примечание: payload кнопки по её подписи (ищем во всех рядах)
def payloadOf(message: Json, text: str) -> str:
    found = [b["payload"] for row in buttons(message) for b in row if b["text"] == text]
    assert found, f"нет кнопки «{text}»: {buttons(message)}"
    return found[0]


# Примечание: все подписи кнопок сообщения подряд
def labels(message: Json) -> list[str]:
    return [b["text"] for row in buttons(message) for b in row]


# Примечание: сервисный токен стенда — из окружения или из .env в корне (как у make up)
def serviceToken() -> str:
    if token := os.environ.get("SERVICE_TOKEN"):
        return token
    env = Path(__file__).resolve().parents[2] / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "SERVICE_TOKEN":
            return value.strip()
    msg = "нет SERVICE_TOKEN ни в окружении, ни в .env"
    raise RuntimeError(msg)


# Примечание: id записи из кнопки «В календаре» (start_param item_<id>)
def itemIdOf(message: Json) -> str:
    found = [
        b["payload"].removeprefix("item_")
        for row in buttons(message)
        for b in row
        if b["type"] == "open_app" and b["payload"].startswith("item_")
    ]
    assert found, f"нет кнопки записи: {buttons(message)}"
    return found[0]


# Примечание: J4 за Scheduler: уведомление о напоминании в чат теста; ответ — тело 200
async def notify(
    client: httpx.AsyncClient, chat: Chat, itemId: str, title: str, remindAt: datetime
) -> Json:
    response = await notifyRaw(client, chat, itemId, title, remindAt)
    assert response.status_code == 200, response.text
    data: Json = response.json()
    return data


# Примечание: один запрос J4 как есть: свой notification_id, номер попытки и X-Trace-Id
async def notifyRaw(
    client: httpx.AsyncClient,
    chat: Chat,
    itemId: str,
    title: str,
    remindAt: datetime,
    *,
    notificationId: str | None = None,
    attempt: int = 1,
    traceId: str | None = None,
) -> httpx.Response:
    body = {
        "notification_id": notificationId or str(uuid.uuid4()),
        "kind": "reminder",
        "user": {"max_user_id": chat.userId, "max_chat_id": chat.chatId, "tz": str(USER_TZ)},
        "item": {
            "id": itemId,
            "kind": "reminder",
            "title": title,
            "starts_at": None,
            "all_day": False,
            "remind_at": remindAt.isoformat(),
            "status": "open",
        },
        "fire_at": remindAt.isoformat(),
        "attempt": attempt,
    }
    headers = {"Authorization": f"Bearer {serviceToken()}"}
    if traceId is not None:
        headers["X-Trace-Id"] = traceId
    return await client.post("/internal/v1/notify", json=body, headers=headers)
