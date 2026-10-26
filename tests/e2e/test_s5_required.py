"""E2E S4, финальный прогон: обязательные сценарии ТЗ — каждый и текстом, и голосом.

Задача и напоминание, встреча, список за период, поиск по старым записям. Плюс «Изменить
время» кнопкой (вопрос → вариант → запись перенесена). Остальные сценарии «плюс» и ошибки — в
test_s1…test_s4; отчёт по всем — в конце прогона (`tests/e2e/conftest.py`).
"""

import httpx
import pytest
from tests.e2e.stand import Chat, given, labels, messagesTo, payloadOf, push, voice

pytestmark = pytest.mark.e2e

CHANNELS = ["text", "voice"]


# Примечание: фраза в чат: текстом или голосом; ответ — строки без «Распознал: …» у голоса
async def say(
    bot: httpx.AsyncClient, chat: Chat, channel: str, phrase: str, count: int
) -> list[str]:
    if channel == "voice":
        await voice(bot, chat, phrase)
    else:
        await push(bot, chat.text(phrase))
    *_, reply = await messagesTo(bot, chat, count)
    lines = str(reply["text"]).split("\n")
    if channel == "voice":
        assert lines[0] == f"Распознал: «{phrase}»", lines
        return lines[1:]
    return lines


# Примечание: ТЗ 1 — напоминание: «Напоминание создано», название, завтра 10:00
@pytest.mark.parametrize("channel", CHANNELS)
async def test_reminder(bot: httpx.AsyncClient, channel: str) -> None:
    lines = await say(bot, Chat(), channel, "Напомни завтра в 10 позвонить Ивану", 1)
    assert lines[:2] == ["Напоминание создано", "Позвонить Ивану"], lines
    assert lines[2].startswith("Завтра, "), lines
    assert lines[2].endswith(", 10:00"), lines


# Примечание: ТЗ 1 — задача без времени: «Задача создана», без срока
@pytest.mark.parametrize("channel", CHANNELS)
async def test_task(bot: httpx.AsyncClient, channel: str) -> None:
    lines = await say(bot, Chat(), channel, "создай задачу купить хлеб", 1)
    assert lines[:2] == ["Задача создана", "Купить хлеб"], lines


# Примечание: ТЗ 2 — встреча: «Встреча создана», завтра 15:00
@pytest.mark.parametrize("channel", CHANNELS)
async def test_meeting(bot: httpx.AsyncClient, channel: str) -> None:
    lines = await say(bot, Chat(), channel, "Встреча с Иваном завтра в 15", 1)
    assert lines[:2] == ["Встреча создана", "Встреча с Иваном"], lines
    assert lines[2].endswith(", 15:00"), lines


# Примечание: ТЗ 3 — список за период: «Что у меня завтра?» → заголовок «Завтра, …»
@pytest.mark.parametrize("channel", CHANNELS)
async def test_listPeriod(bot: httpx.AsyncClient, channel: str) -> None:
    lines = await say(bot, Chat(), channel, "Что у меня завтра?", 1)
    assert lines[0].startswith("Завтра, "), lines


# Примечание: ТЗ 4 — поиск по старым записям: «найди звонок Ивану» → «Нашёл:» и запись
@pytest.mark.parametrize("channel", CHANNELS)
async def test_search(bot: httpx.AsyncClient, channel: str) -> None:
    chat = Chat()
    await given(bot, chat, "Напомни завтра в 10 позвонить Ивану")
    lines = await say(bot, chat, channel, "найди звонок Ивану", 2)
    assert lines[0].startswith("Нашёл"), lines
    assert any("Позвонить Ивану" in line for line in lines[1:]), lines


# Примечание: «Изменить время» — вопрос с вариантами → «Завтра в 9:00» → запись перенесена
async def test_rescheduleButton(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await push(bot, chat.text("Напомни завтра в 10 позвонить Ивану"))
    [created] = await messagesTo(bot, chat, 1)
    await push(bot, chat.press(payloadOf(created, "Изменить время")))
    _, asked = await messagesTo(bot, chat, 2)
    assert str(asked["text"]).startswith("На когда перенести?"), asked["text"]
    assert "Завтра в 9:00" in labels(asked), labels(asked)
    await push(bot, chat.press(payloadOf(asked, "Завтра в 9:00")))
    *_, moved = await messagesTo(bot, chat, 3)
    lines = str(moved["text"]).split("\n")
    assert lines[:2] == ["Изменено", "Позвонить Ивану"], lines
    assert lines[2].endswith(", 09:00"), lines
