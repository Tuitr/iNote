from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from bot.domain.updates import MaxChatId
from common.markdown import cutText

type MessageId = str

# Лимиты MAX: текст сообщения; в ряду клавиатуры до 7 кнопок и до 3 ссылок или open_app
MAX_TEXT_LENGTH: Final = 4000
MAX_BUTTONS_PER_ROW: Final = 7
MAX_APP_BUTTONS_PER_ROW: Final = 3
MAX_KEYBOARD_ROWS: Final = 30


# Примечание: разметка текста; plain — без разметки, пользовательский текст не ломает сообщение
class MessageFormat(StrEnum):
    PLAIN = "plain"
    MARKDOWN = "markdown"
    HTML = "html"


# Примечание: вид кнопки MAX: callback → POST /answers и апдейт боту; link и open_app — без бота
class ButtonKind(StrEnum):
    CALLBACK = "callback"
    LINK = "link"
    OPEN_APP = "open_app"


# Примечание: кнопка клавиатуры в терминах MAX, но без JSON: payload — у callback и open_app
@dataclass(frozen=True)
class KeyboardButton:
    kind: ButtonKind
    text: str
    payload: str | None = None
    url: str | None = None
    webApp: str | None = None

    # Примечание: у ссылки обязателен url, у callback — payload
    def __post_init__(self) -> None:
        if not self.text:
            msg = "кнопка без текста"
            raise ValueError(msg)
        if self.kind is ButtonKind.LINK and not self.url:
            msg = "кнопка-ссылка без url"
            raise ValueError(msg)
        if self.kind is ButtonKind.CALLBACK and not self.payload:
            msg = "callback-кнопка без payload"
            raise ValueError(msg)

    # Примечание: ссылки и open_app MAX ограничивает отдельно — до 3 в ряду
    @property
    def isAppLike(self) -> bool:
        return self.kind in {ButtonKind.LINK, ButtonKind.OPEN_APP}


type Keyboard = tuple[tuple[KeyboardButton, ...], ...]


# Примечание: исходящее сообщение; длину и клавиатуру проверяем здесь, чтобы MAX не вернул 400
@dataclass(frozen=True)
class OutgoingMessage:
    chatId: MaxChatId
    text: str
    format: MessageFormat = MessageFormat.PLAIN
    keyboard: Keyboard = ()

    # Примечание: пустое или длиннее 4000 символов, кривая клавиатура — ошибка в коде, а не в MAX
    def __post_init__(self) -> None:
        if not self.text:
            msg = "пустой текст сообщения"
            raise ValueError(msg)
        if len(self.text) > MAX_TEXT_LENGTH:
            msg = f"текст {len(self.text)} символов, лимит MAX {MAX_TEXT_LENGTH}"
            raise ValueError(msg)
        _checkKeyboard(self.keyboard)


# Примечание: ряды непустые, до 7 кнопок и до 3 ссылок/open_app в ряду, рядов не больше 30
def _checkKeyboard(keyboard: Keyboard) -> None:
    if len(keyboard) > MAX_KEYBOARD_ROWS:
        msg = f"{len(keyboard)} рядов кнопок, лимит {MAX_KEYBOARD_ROWS}"
        raise ValueError(msg)
    for index, row in enumerate(keyboard):
        if not row or len(row) > MAX_BUTTONS_PER_ROW:
            msg = f"ряд {index}: {len(row)} кнопок (1…{MAX_BUTTONS_PER_ROW})"
            raise ValueError(msg)
        if sum(button.isAppLike for button in row) > MAX_APP_BUTTONS_PER_ROW:
            msg = f"ряд {index}: больше {MAX_APP_BUTTONS_PER_ROW} ссылок или open_app"
            raise ValueError(msg)


# Примечание: обрезает текст до лимита MAX с многоточием; экранирование markdown не рвёт
def fitToLimit(text: str, limit: int = MAX_TEXT_LENGTH) -> str:
    return cutText(text, limit)
