"""Пределы клавиатуры MAX (S4, шаг 4): при любом допустимом Reply сообщение бота — не больше
30 рядов, 7 кнопок и 3 open_app/ссылок в ряду, в обоих режимах BOT_OPEN_APP_MODE.

Вместо hypothesis — детерминированный генератор Reply по seed.
"""

import random
import uuid
from datetime import date, timedelta
from typing import Final

import pytest

from bot.application.reply_mapper import OpenAppMode, ReplyToMaxMapper
from bot.domain.messages import MAX_APP_BUTTONS_PER_ROW, MAX_BUTTONS_PER_ROW, MAX_KEYBOARD_ROWS
from contracts.reply import (
    MAX_BUTTON_ROWS,
    MAX_OPEN_APP_PER_ROW,
    ActionButton,
    OpenAppButton,
    Reply,
    ReplyButton,
    ReplyFormat,
    ReplyState,
)
from contracts.start_param import StartParam
from services.bot.tests.fakes import InMemoryCallbackStore

SEED: Final = 20261023
CASES: Final = 60
CHAT: Final = 500600700
TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"


# Примечание: случайный start_param J6 из всех видов
def randomParam(rnd: random.Random) -> StartParam:
    day = date(2026, 1, 5) + timedelta(weeks=rnd.randint(0, 500))
    return rnd.choice(
        [
            StartParam.today(),
            StartParam.dialog(),
            StartParam.forDay(day + timedelta(days=rnd.randint(0, 6))),
            StartParam.forWeek(day),
            StartParam.forItem(uuid.UUID(int=rnd.getrandbits(128), version=4)),
        ]
    )


# Примечание: случайный допустимый ряд: 1…7 кнопок, из них не больше 3 open_app
def randomRow(rnd: random.Random) -> list[ReplyButton]:
    size = rnd.randint(1, MAX_BUTTONS_PER_ROW)
    apps = rnd.randint(0, min(size, MAX_OPEN_APP_PER_ROW))
    row: list[ReplyButton] = [
        OpenAppButton(text="я" * rnd.randint(1, 60), startParam=str(randomParam(rnd)))
        for _ in range(apps)
    ]
    row += [
        ActionButton(text=f"Кнопка {n}", action="undo", args={"item_id": str(uuid.uuid4())})
        for n in range(size - apps)
    ]
    rnd.shuffle(row)
    return row


# Примечание: случайный допустимый Reply: до 30 рядов
def randomReply(rnd: random.Random) -> Reply:
    rows = [randomRow(rnd) for _ in range(rnd.randint(0, MAX_BUTTON_ROWS))]
    return Reply(
        text="Ответ",
        format=ReplyFormat.PLAIN,
        state=ReplyState.ANSWER,
        buttons=rows,
        traceId=TRACE,
    )


# Примечание: любой допустимый Reply → клавиатура в лимитах MAX, ни одна кнопка не потеряна
@pytest.mark.parametrize("mode", list(OpenAppMode))
async def test_anyReplyFitsMaxKeyboard(mode: OpenAppMode) -> None:
    rnd = random.Random(SEED)  # noqa: S311 — не криптография
    mapper = ReplyToMaxMapper(InMemoryCallbackStore(), mode=mode, botUsername="va_bot")
    for _ in range(CASES):
        reply = randomReply(rnd)
        message = await mapper.toMessage(reply, CHAT)
        assert len(message.keyboard) <= MAX_KEYBOARD_ROWS
        for row in message.keyboard:
            assert 1 <= len(row) <= MAX_BUTTONS_PER_ROW
            assert sum(button.isAppLike for button in row) <= MAX_APP_BUTTONS_PER_ROW
        assert sum(map(len, message.keyboard)) == sum(map(len, reply.buttons))
