"""J8 · agent (C) → mcp-контейнер (D): POST /internal/v1/dialog-turns. Владелец D, черновик C к G1.

После каждого хода и действия агент пишет пару реплик в историю диалога (dialog_log у D):
что сказал пользователь (текст, голос или кнопка) и что ответил ассистент (текст и id записей).
Вызов идемпотентен по trace_id: повтор не создаёт дубль. Ответ — 202, тело агент не читает.
"""

import uuid
from typing import Final, Literal

from pydantic import Field

from common.wire import WireModel

DIALOG_TURNS_PATH: Final = "/internal/v1/dialog-turns"
TEXT_MAX_LENGTH: Final = 4000

# как у D (libs/contracts/internal.py в main): голосом или текстом; кнопка и команда — text,
# в text — подпись кнопки («Отменить») или команда («/week»)
type DialogInputType = Literal["text", "voice"]


# Примечание: реплика пользователя; для кнопки — её подпись, для команды — сама команда
class UserDialogTurn(WireModel):
    role: Literal["user"] = "user"
    text: str = Field(max_length=TEXT_MAX_LENGTH)
    inputType: DialogInputType


# Примечание: реплика ассистента — текст ответа и записи, о которых он (Reply.items)
class AssistantDialogTurn(WireModel):
    role: Literal["assistant"] = "assistant"
    text: str = Field(max_length=TEXT_MAX_LENGTH)
    itemIds: list[uuid.UUID] = Field(default_factory=list)


# Примечание: один ход: внутренний user_id (не max_user_id), trace_id цепочки и время ответа
class DialogTurnsRequest(WireModel):
    userId: int = Field(gt=0)
    traceId: str = Field(min_length=1, max_length=64)
    turns: tuple[UserDialogTurn, AssistantDialogTurn]
    latencyMs: int = Field(ge=0)
