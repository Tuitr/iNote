from datetime import datetime
from typing import Final

import pytest
from pydantic import ValidationError

from contracts.reply import (
    ActionButton,
    ActionRequest,
    MessageRequest,
    OpenAppButton,
    Reply,
    ReplyState,
)
from libs.contracts.tests.examples import loadExample

REPLY_EXAMPLES: Final = ["reply/confirm.json", "reply/list.json", "reply/error.json"]
TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"


# Примечание: каждый пример Reply валиден и сериализуется обратно байт в байт по смыслу
@pytest.mark.parametrize("name", REPLY_EXAMPLES)
def test_replyExamplesRoundTrip(name: str) -> None:
    raw = loadExample(name)
    assert Reply.fromWire(raw).toWire() == raw


# Примечание: примеры запросов бота к агенту
@pytest.mark.parametrize(
    ("name", "model"),
    [("agent/message_request.json", MessageRequest), ("agent/action_request.json", ActionRequest)],
)
def test_requestExamplesRoundTrip(name: str, model: type[MessageRequest | ActionRequest]) -> None:
    raw = loadExample(name)
    assert model.fromWire(raw).toWire() == raw


# Примечание: кнопки различаются по kind; open_app несёт start_param строго по J6
def test_buttonsDiscriminated() -> None:
    reply = Reply.fromWire(loadExample("reply/confirm.json"))
    first, second = reply.buttons
    assert isinstance(first[0], ActionButton)
    assert first[0].action == "undo"
    assert isinstance(second[0], OpenAppButton)
    assert second[0].startParam.startswith("item_")


# Примечание: незнакомые поля игнорируются (правило 8)
def test_unknownFieldsIgnored() -> None:
    raw = loadExample("reply/error.json")
    assert isinstance(raw, dict)
    reply = Reply.fromWire({**raw, "future_field": 1})
    assert reply.state is ReplyState.ERROR


# Примечание: время без смещения — ошибка (правило 2)
def test_naiveDatetimeRejected() -> None:
    raw = loadExample("reply/confirm.json")
    assert isinstance(raw, dict)
    items = raw["items"]
    assert isinstance(items, list)
    items[0]["starts_at"] = "2026-10-18T10:00:00"
    with pytest.raises(ValidationError, match="timezone"):
        Reply.fromWire(raw)


# Примечание: ограничения MAX на клавиатуру и текст проверяются в контракте
@pytest.mark.parametrize(
    ("buttons", "text", "error"),
    [
        ([[ActionButton(text=str(i), action="undo") for i in range(8)]], "ok", "MAX допускает 7"),
        (
            [[OpenAppButton(text=str(i), startParam="today") for i in range(4)]],
            "ok",
            "допускает 3",
        ),
        ([[]], "ok", "пустой"),
        ([], "я" * 4001, "4000"),
    ],
)
def test_limits(buttons: list[list[ActionButton | OpenAppButton]], text: str, error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        Reply(text=text, state=ReplyState.DONE, buttons=buttons, traceId=TRACE)


# Примечание: start_param не по J6 и кривое имя действия отклоняются
def test_badButtonValues() -> None:
    with pytest.raises(ValidationError):
        OpenAppButton(text="x", startParam="week_2026-10-21")
    with pytest.raises(ValidationError):
        ActionButton(text="x", action="Undo!")


# Примечание: даты сериализуются ISO 8601 со смещением как пришли
def test_datetimeKeepsOffset() -> None:
    reply = Reply.fromWire(loadExample("reply/list.json"))
    startsAt = reply.items[0].startsAt
    assert isinstance(startsAt, datetime)
    assert startsAt.utcoffset() is not None
    items = reply.toWire()["items"]
    assert isinstance(items, list)
    assert items[0]["starts_at"] == "2026-10-21T10:00:00+03:00"
