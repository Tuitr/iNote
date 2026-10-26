"""OutgoingMessage → тело POST /messages MAX (NewMessageBody) — по схеме max-bot-api-client-go."""

from bot.domain.messages import ButtonKind, KeyboardButton, MessageFormat, OutgoingMessage

INLINE_KEYBOARD = "inline_keyboard"
TYPING_ON = "typing_on"


# Примечание: NewMessageBody: text, format (plain — без поля), attachments с inline_keyboard
def toNewMessageBody(message: OutgoingMessage) -> dict[str, object]:
    body: dict[str, object] = {"text": message.text, "attachments": attachments(message)}
    if message.format is not MessageFormat.PLAIN:
        body["format"] = message.format.value
    return body


# Примечание: вложения сообщения: клавиатура — одно вложение inline_keyboard
def attachments(message: OutgoingMessage) -> list[dict[str, object]]:
    if not message.keyboard:
        return []
    rows = [[_button(button) for button in row] for row in message.keyboard]
    return [{"type": INLINE_KEYBOARD, "payload": {"buttons": rows}}]


# Примечание: тело POST /chats/{chat_id}/actions для «печатает…»
def typingBody() -> dict[str, object]:
    return {"action": TYPING_ON}


# Примечание: кнопка MAX: callback {payload}, link {url}, open_app {web_app?, payload?}
def _button(button: KeyboardButton) -> dict[str, object]:
    raw: dict[str, object] = {"type": button.kind.value, "text": button.text}
    match button.kind:
        case ButtonKind.CALLBACK:
            raw["payload"] = button.payload
        case ButtonKind.LINK:
            raw["url"] = button.url
        case ButtonKind.OPEN_APP:
            if button.webApp:
                raw["web_app"] = button.webApp
            if button.payload:
                raw["payload"] = button.payload
    return raw
