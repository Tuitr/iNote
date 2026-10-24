"""Демо зоны C на стенде `mock` (S4): диалог в фейковом MAX, напечатанный как чат, на 3–5 минут.

Свой пользователь и чат на каждый запуск; данные — заглушки (MCP C с фикстурами, ASR «OggS» +
текст). Сценарий: приветствие → напоминание голосом → встреча с конфликтом и «Всё равно создать»
→ «что у меня завтра» → поиск → уведомление J4 → «Отложить на 1 ч» → «перенеси его на завтра в
15» голосом → /settings и пояс → «что у меня сегодня». Под каждым ответом — кнопки и trace_id.

    ./deploy/demo.sh                 # пауза между шагами DEMO_PAUSE_S (по умолчанию 2 с)
    DEMO_PAUSE_S=0 ./deploy/demo.sh  # без пауз
"""

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.e2e.stand import (
    Chat,
    buttons,
    itemIdOf,
    messagesTo,
    notifyRaw,
    payloadOf,
    push,
    voice,
)

BOT_URL: Final = os.environ.get("BOT_URL", "http://127.0.0.1:8001")
PAUSE_S: Final = float(os.environ.get("DEMO_PAUSE_S", "2"))


# Примечание: диалог одного чата: печатает реплики и ответы, считает сообщения бота
class Dialog:
    # Примечание: client — к dev-роутам бота
    def __init__(self, client: httpx.AsyncClient) -> None:
        self.client = client
        self.chat = Chat()
        self.seen = 0
        self.last: dict[str, object] = {}

    # Примечание: текстом от пользователя
    async def text(self, phrase: str) -> None:
        show(f"🧑 {phrase}")
        await push(self.client, self.chat.text(phrase))
        await self.answer()

    # Примечание: голосом от пользователя (ASR-заглушка «распознает» этот текст)
    async def voice(self, phrase: str) -> None:
        show(f"🧑 🎤 «{phrase}»")
        await voice(self.client, self.chat, phrase)
        await self.answer()

    # Примечание: нажатие кнопки по подписи в последнем ответе
    async def press(self, label: str) -> None:
        show(f"🧑 [нажал «{label}»]")
        await push(self.client, self.chat.press(payloadOf(self.last, label)))
        await self.answer()

    # Примечание: уведомление J4 о записи, как его прислал бы Scheduler D
    async def notify(self, itemId: str, title: str) -> None:
        show("⏰ Scheduler → бот: POST /internal/v1/notify")
        remindAt = datetime.now(tz=UTC) + timedelta(minutes=1)
        response = await notifyRaw(self.client, self.chat, itemId, title, remindAt)
        response.raise_for_status()
        await self.answer()

    # Примечание: ждём новый ответ бота и печатаем его с кнопками и trace_id
    async def answer(self) -> None:
        messages = await messagesTo(self.client, self.chat, self.seen + 1)
        for message in messages[self.seen :]:
            show(indent(f"🤖 {message['text']}"))
            for row in buttons(message):
                show(indent("   " + "  ".join(f"[{b['text']}]" for b in row)))
            show(indent(f"   trace_id {message.get('trace_id')}"))
            self.last = message
        self.seen = len(messages)
        await asyncio.sleep(PAUSE_S)


# Примечание: строка в вывод сразу (демо идёт вживую)
def show(line: str) -> None:
    print(line, flush=True)  # noqa: T201 — вывод демо


# Примечание: многострочный ответ бота — с отступом, как в чате
def indent(text: str) -> str:
    return text.replace("\n", "\n     ")


# Примечание: весь сценарий по шагам
async def main() -> None:
    async with httpx.AsyncClient(base_url=BOT_URL, timeout=30) as client:
        (await client.get("/health/ready")).raise_for_status()
        demo = Dialog(client)
        show("— Демо: голосовой ассистент для календаря в MAX (стенд mock) —\n")
        await demo.text("/start")
        await demo.voice("Напомни через минуту позвонить Ивану")
        reminderId = itemIdOf(demo.last)
        # стенд на настоящем D: у нового пользователя записей нет — конфликту нужна своя встреча
        await demo.text("Встреча со стоматологом завтра в 9:30")
        await demo.voice("Встреча с Петром завтра в 9:30")
        await demo.press("Всё равно создать")
        await demo.text("Что у меня завтра?")
        await demo.text("найди звонок Ивану")
        await demo.notify(reminderId, "Позвонить Ивану")
        await demo.press("Отложить на 1 ч")
        await demo.voice("перенеси его на завтра в 15")
        await demo.text("/settings")
        await demo.press("Екатеринбург")
        await demo.text("что у меня сегодня")
        show("\n— Конец демо. Путь любого шага: make logs TRACE=<первые 6 символов trace_id> —")


if __name__ == "__main__":
    asyncio.run(main())
