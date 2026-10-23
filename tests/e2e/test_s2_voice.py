"""E2E S2 на стенде голосом: фейковый MAX → бот → ASR-заглушка → агент → ScriptedBrain →
MCP-заглушка → бот. Четыре обязательных сценария ТЗ и уточнения кнопками.

Запуск: `make e2e` (поднимает стенд) или `uv run pytest -m e2e` при уже поднятом стенде.
Работает и в режиме webhook: фейковый MAX доставляет апдейты POST-ом на /webhook/max.
"""

from datetime import datetime, timedelta

import httpx
import pytest
from tests.e2e.stand import USER_TZ, Chat, labels, messagesTo, payloadOf, push, voice

pytestmark = pytest.mark.e2e

HEARD = "Распознал: «{text}»"


# Примечание: строки ответа без первой «Распознал: …» — она проверяется отдельно
def body(message: dict[str, object], said: str) -> list[str]:
    lines = str(message["text"]).split("\n")
    assert lines[0] == HEARD.format(text=said), lines[0]
    return lines[1:]


# Примечание: «завтра» в поясе пользователя — для проверки дат в ответах
def tomorrowDay() -> int:
    return (datetime.now(tz=USER_TZ) + timedelta(days=1)).day


# Примечание: ТЗ 1 — «Напомни завтра в 10 позвонить Ивану» → распознано, подтверждение из
# сохранённых данных, кнопки «Отменить» и «Изменить время»; «Отменить» → «Отменено»
async def test_voiceReminder(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    said = "Напомни завтра в 10 позвонить Ивану"
    await voice(bot, chat, said)
    [reply] = await messagesTo(bot, chat, 1)
    lines = body(reply, said)
    assert lines[:2] == ["Напоминание создано", "Позвонить Ивану"]
    assert lines[2].startswith("Завтра, ")
    assert lines[2].endswith(", 10:00")
    assert f" {tomorrowDay()} " in lines[2]
    assert labels(reply)[:2] == ["Отменить", "Изменить время"]
    await push(bot, chat.press(payloadOf(reply, "Отменить")))
    _, undone = await messagesTo(bot, chat, 2)
    assert str(undone["text"]).startswith("Отменено\nПозвонить Ивану")


# Примечание: ТЗ 2 — «Встреча с Иваном завтра в 15» → встреча создана
async def test_voiceMeeting(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    said = "Встреча с Иваном завтра в 15"
    await voice(bot, chat, said)
    [reply] = await messagesTo(bot, chat, 1)
    lines = body(reply, said)
    assert lines[:2] == ["Встреча создана", "Встреча с Иваном"]
    assert lines[2].endswith(", 15:00")


# Примечание: ТЗ 2, конфликт — на завтра 9:30 уже «Стоматолог»: кнопки свободных окон →
# выбор → resume → встреча создана в выбранное окно
async def test_voiceMeetingConflict(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    said = "Встреча с Петром завтра в 9:30"
    await voice(bot, chat, said)
    [asked] = await messagesTo(bot, chat, 1)
    lines = body(asked, said)
    assert lines[0].startswith("В это время уже есть: «Стоматолог»")
    slots, cancel = labels(asked)[:-1], labels(asked)[-1]
    assert slots
    assert cancel == "Отмена"
    await push(bot, chat.press(payloadOf(asked, slots[0])))
    _, created = await messagesTo(bot, chat, 2)
    text = str(created["text"]).split("\n")
    assert text[:2] == ["Встреча создана", "Встреча с Петром"]
    assert text[2].endswith(f", {slots[0].split('–')[0]}")


# Примечание: ТЗ 3 — «Что у меня завтра?» → список на завтра по дням
async def test_voiceTomorrow(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    said = "Что у меня завтра?"
    await voice(bot, chat, said)
    [reply] = await messagesTo(bot, chat, 1)
    lines = body(reply, said)
    assert lines[0].startswith("Завтра, ")
    assert f" {tomorrowDay()} " in lines[0]
    assert len(lines) > 1
    assert reply["format"] == "markdown"


# Примечание: ТЗ 4 — «Найди звонок Ивану» → результат поиска
async def test_voiceSearch(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    said = "Найди звонок Ивану"
    await voice(bot, chat, said)
    [reply] = await messagesTo(bot, chat, 1)
    lines = body(reply, said)
    assert lines[0] == "Нашёл:"
    assert any("Позвонить Ивану" in line for line in lines[1:])


# Примечание: «Напомни позвонить маме» без времени → вопрос с вариантами → кнопка → создано
async def test_voiceAsksTime(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    said = "Напомни позвонить маме"
    await voice(bot, chat, said)
    [asked] = await messagesTo(bot, chat, 1)
    assert body(asked, said)[0].startswith("На какое время?")
    options = labels(asked)
    assert options[-1] == "Отмена"
    await push(bot, chat.press(payloadOf(asked, options[0])))
    _, created = await messagesTo(bot, chat, 2)
    assert str(created["text"]).startswith("Напоминание создано\nПозвонить маме")


# Примечание: ASR не уверен → «Я правильно понял…?» → «Да» → выполнено
async def test_voiceLowConfidence(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await voice(bot, chat, "~low~ напомни завтра в 10 купить хлеб")
    [asked] = await messagesTo(bot, chat, 1)
    assert "Я правильно понял" in str(asked["text"])
    await push(bot, chat.press(payloadOf(asked, "Да")))
    _, created = await messagesTo(bot, chat, 2)
    assert str(created["text"]).startswith("Напоминание создано\nКупить хлеб")


# Примечание: тишина (no_speech) — текст бота с кодом, агент не нужен
async def test_voiceNoSpeech(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await voice(bot, chat, "")
    [reply] = await messagesTo(bot, chat, 1)
    text = str(reply["text"])
    assert text.startswith("Не расслышал")
    assert "Код: " in text


# Примечание: /week — список недели без LLM (/v1/actions list_period)
async def test_weekCommand(bot: httpx.AsyncClient) -> None:
    chat = Chat()
    await push(bot, chat.text("/week"))
    [reply] = await messagesTo(bot, chat, 1)
    assert str(reply["text"]).startswith("Неделя, ")
    assert "Открыть неделю" in labels(reply)
