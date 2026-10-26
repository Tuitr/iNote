"""E2E S4: отказ соседа на стенде — пользователь получает понятный текст без кода, а не тишину.

Контейнер останавливается на время теста (`stopped`) и поднимается обратно; после возврата
всё работает без перезапуска бота и агента. Тесты меняют общий стенд — идут по одному
(pytest без -n), каждый восстанавливает сервис в finally.
"""

import asyncio
import re
from typing import Final

import httpx
import pytest
from tests.e2e.stand import Chat, messagesTo, push, stopped, voice

pytestmark = pytest.mark.e2e

CODE_RE: Final = re.compile(r"Код: [0-9a-f]{6}$")
RECOVER_S: Final = 30.0
TYPING_INTERVAL_S: Final = 4.0


# Примечание: последнее сообщение бота в чат — текст
async def lastText(bot: httpx.AsyncClient, chat: Chat, count: int) -> str:
    *_, last = await messagesTo(bot, chat, count)
    return str(last["text"])


# Примечание: сколько раз бот показывал «печатает…» в этот чат
async def typingCount(bot: httpx.AsyncClient, chat: Chat) -> int:
    typing = (await bot.get("/dev/max/sent")).json()["typing"]
    return sum(1 for chatId in typing if chatId == chat.chatId)


# Примечание: после возврата соседа фраза снова проходит (повторяем, пока сосед прогревается)
async def recovered(bot: httpx.AsyncClient, phrase: str, expected: str) -> None:
    async with asyncio.timeout(RECOVER_S):
        while True:
            chat = Chat()
            await push(bot, chat.text(phrase))
            if (await lastText(bot, chat, 1)).startswith(expected):
                return
            await asyncio.sleep(1)


# Примечание: агент остановлен → «Не получилось связаться с ассистентом…», «печатает…» погас
async def test_agentDown(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    with stopped("agent"):
        await push(bot, chat.text("что у меня сегодня"))
        text = await lastText(bot, chat, 1)
        typingAfterReply = await typingCount(bot, chat)
        await asyncio.sleep(TYPING_INTERVAL_S + 0.5)
        assert await typingCount(bot, chat) == typingAfterReply
    assert text.startswith("Не получилось связаться с ассистентом"), text
    assert not CODE_RE.search(text), text
    await recovered(bot, "что у меня сегодня", "Сегодня, ")


# Примечание: ASR остановлен → голосовое: «Не получилось распознать голосовое…»
async def test_asrDown(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    with stopped("asr"):
        await voice(bot, chat, "что у меня завтра")
        text = await lastText(bot, chat, 1)
    assert text.startswith("Не получилось распознать голосовое"), text
    assert not CODE_RE.search(text), text


# Примечание: MCP-контейнер (MCP и J8) остановлен: ход с инструментом — «Сервис записей сейчас
# недоступен…»; пользователь из кеша получает /help без J8; новый (кеш пуст) — ошибку;
# после возврата — работает, лок не залип
async def test_mcpAndJ8Down(bot: httpx.AsyncClient) -> None:
    known = Chat()
    await push(bot, known.text("что у меня сегодня"))  # кеш пользователя заполнен
    assert (await lastText(bot, known, 1)).startswith("Сегодня, ")
    stranger = Chat()
    with stopped("mcp"):
        await push(bot, known.text("что у меня завтра"))
        toolDown = await lastText(bot, known, 2)
        await push(bot, known.text("/help"))
        cachedHelp = await lastText(bot, known, 3)
        await push(bot, stranger.text("/help"))
        noCache = await lastText(bot, stranger, 1)
    assert toolDown.startswith("Сервис записей"), toolDown
    assert not CODE_RE.search(toolDown), toolDown
    assert cachedHelp.startswith("Пиши обычным текстом или голосом"), cachedHelp
    assert noCache.startswith("Сервис записей"), noCache
    assert not CODE_RE.search(noCache), noCache
    await recovered(bot, "что у меня сегодня", "Сегодня, ")


# Примечание: Redis остановлен → бот отвечает «Что-то пошло не так…» (не тишина), бот и
# агент не падают; Redis вернулся — работает без перезапуска
async def test_redisDown(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    with stopped("redis"):
        await push(bot, chat.text("что у меня сегодня"))
        text = await lastText(bot, chat, 1)
        live = (await bot.get("/health/live")).status_code
    assert text.startswith("Что-то пошло не так"), text
    assert not CODE_RE.search(text), text
    assert live == 200
    await recovered(bot, "что у меня сегодня", "Сегодня, ")
