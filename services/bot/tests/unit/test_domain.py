import pytest

from bot.domain.messages import MAX_TEXT_LENGTH, OutgoingMessage, fitToLimit


# Примечание: пустое и слишком длинное сообщение — ошибка в коде, до MAX не доходит
@pytest.mark.parametrize("text", ["", "x" * (MAX_TEXT_LENGTH + 1)])
def test_outgoingMessageValidated(text: str) -> None:
    with pytest.raises(ValueError, match=r"текст|пустой"):
        OutgoingMessage(chatId=1, text=text)


# Примечание: ровно 4000 символов допустимо
def test_outgoingMessageAtLimit() -> None:
    assert len(OutgoingMessage(chatId=1, text="x" * MAX_TEXT_LENGTH).text) == MAX_TEXT_LENGTH


# Примечание: обрезка с многоточием только при превышении
def test_fitToLimit() -> None:
    assert fitToLimit("abc", limit=3) == "abc"
    assert fitToLimit("abcd", limit=3) == "ab…"
