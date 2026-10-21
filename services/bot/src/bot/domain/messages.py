from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from bot.domain.updates import MaxChatId

type MessageId = str

# Лимит MAX на текст сообщения
MAX_TEXT_LENGTH: Final = 4000


# Примечание: разметка текста; plain — без разметки, пользовательский текст не ломает сообщение
class MessageFormat(StrEnum):
    PLAIN = "plain"
    MARKDOWN = "markdown"
    HTML = "html"


# Примечание: исходящее сообщение; длину проверяем здесь, чтобы MAX не вернул 400
@dataclass(frozen=True)
class OutgoingMessage:
    chatId: MaxChatId
    text: str
    format: MessageFormat = MessageFormat.PLAIN

    # Примечание: пустое или длиннее 4000 символов — ошибка в коде, а не в MAX
    def __post_init__(self) -> None:
        if not self.text:
            msg = "пустой текст сообщения"
            raise ValueError(msg)
        if len(self.text) > MAX_TEXT_LENGTH:
            msg = f"текст {len(self.text)} символов, лимит MAX {MAX_TEXT_LENGTH}"
            raise ValueError(msg)


# Примечание: обрезает текст до лимита MAX с многоточием
def fitToLimit(text: str, limit: int = MAX_TEXT_LENGTH) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
