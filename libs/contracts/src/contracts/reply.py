"""API агента для бота (внутри зоны C): запросы бота и абстрактный ответ Reply. Черновик к G1.

Бот переводит Reply в формат MAX: action — в callback-кнопку, open_app — в кнопку мини-приложения.
Читатель игнорирует незнакомые поля (правило 8 CLAUDE.md).
"""

import uuid
from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import AwareDatetime, Field, field_validator

from common.wire import WireModel
from contracts.start_param import StartParamStr

REPLY_TEXT_MAX_LENGTH: Final = 4000
BUTTON_TEXT_MAX_LENGTH: Final = 128
MAX_BUTTONS_PER_ROW: Final = 7
MAX_OPEN_APP_PER_ROW: Final = 3
MAX_BUTTON_ROWS: Final = 30
ACTION_PATTERN: Final = r"^[a-z][a-z0-9_]{0,63}$"

type ArgValue = str | int | bool


# Примечание: разметка текста Reply; названия от пользователя в markdown экранирует агент
class ReplyFormat(StrEnum):
    PLAIN = "plain"
    MARKDOWN = "markdown"


# Примечание: итог хода — совпадает с TurnResult.outcome (J2)
class ReplyState(StrEnum):
    DONE = "done"
    CLARIFY = "clarify"
    ANSWER = "answer"
    ERROR = "error"


# Примечание: кнопка-действие → callback-кнопка MAX → POST /v1/actions без LLM
class ActionButton(WireModel):
    kind: Literal["action"] = "action"
    text: str = Field(min_length=1, max_length=BUTTON_TEXT_MAX_LENGTH)
    action: str = Field(pattern=ACTION_PATTERN)
    args: dict[str, ArgValue] = Field(default_factory=dict)


# Примечание: кнопка мини-приложения; start_param строго по J6
class OpenAppButton(WireModel):
    kind: Literal["open_app"] = "open_app"
    text: str = Field(min_length=1, max_length=BUTTON_TEXT_MAX_LENGTH)
    startParam: StartParamStr


type ReplyButton = Annotated[ActionButton | OpenAppButton, Field(discriminator="kind")]


# Примечание: краткая запись для ответа: то, что реально сохранено (правило 3)
class ReplyItem(WireModel):
    id: uuid.UUID
    kind: Literal["task", "reminder", "event"]
    title: str = Field(min_length=1)
    startsAt: AwareDatetime | None = None
    endsAt: AwareDatetime | None = None
    allDay: bool = False
    status: Literal["open", "done"] = "open"


# Примечание: ответ агента боту; бот не знает бизнес-логики, агент — формата MAX
class Reply(WireModel):
    text: str = Field(min_length=1, max_length=REPLY_TEXT_MAX_LENGTH)
    format: ReplyFormat = ReplyFormat.PLAIN
    state: ReplyState
    buttons: list[list[ReplyButton]] = Field(default_factory=list, max_length=MAX_BUTTON_ROWS)
    items: list[ReplyItem] = Field(default_factory=list)
    speak: bool = False
    traceId: str = Field(pattern=r"^[0-9a-f]{32}$")

    # Примечание: ограничения клавиатуры MAX: до 7 кнопок и до 3 open_app в ряду, пустых рядов нет
    @field_validator("buttons")
    @classmethod
    def _checkKeyboard(cls, rows: list[list[ReplyButton]]) -> list[list[ReplyButton]]:
        for index, row in enumerate(rows):
            if not row:
                msg = f"ряд кнопок {index} пустой"
                raise ValueError(msg)
            if len(row) > MAX_BUTTONS_PER_ROW:
                msg = f"ряд {index}: {len(row)} кнопок, MAX допускает {MAX_BUTTONS_PER_ROW}"
                raise ValueError(msg)
            openApps = sum(isinstance(b, OpenAppButton) for b in row)
            if openApps > MAX_OPEN_APP_PER_ROW:
                msg = f"ряд {index}: {openApps} open_app, MAX допускает {MAX_OPEN_APP_PER_ROW}"
                raise ValueError(msg)
        return rows


# Примечание: текст (или распознанное голосовое) пользователя → POST /v1/messages
class MessageRequest(WireModel):
    maxUserId: int
    maxChatId: int
    text: str = Field(min_length=1, max_length=REPLY_TEXT_MAX_LENGTH)
    source: Literal["text", "voice"] = "text"
    locale: str | None = None


# Примечание: нажатие кнопки или команда без LLM → POST /v1/actions
class ActionRequest(WireModel):
    maxUserId: int
    maxChatId: int
    action: str = Field(pattern=ACTION_PATTERN)
    args: dict[str, ArgValue] = Field(default_factory=dict)
    callbackId: str | None = None
