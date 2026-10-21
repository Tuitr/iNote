from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


# Примечание: запись журнала отправленного сообщения (фейковый MAX)
@dataclass(frozen=True)
class SentMessageRecord:
    messageId: str
    chatId: int
    text: str
    format: str


# Примечание: запись журнала ответа на нажатие кнопки
@dataclass(frozen=True)
class CallbackAnswerRecord:
    callbackId: str
    notification: str | None


# Примечание: dev-порт фейкового MAX для /dev/max/*; в режиме real его нет вовсе
class DevMaxConsole(Protocol):
    # Примечание: подложить сырой апдейт MAX, вернуть его позицию в очереди
    async def pushRaw(self, raw: Mapping[str, object]) -> int: ...

    # Примечание: всё, что бот отправил
    def sentMessages(self) -> tuple[SentMessageRecord, ...]: ...

    # Примечание: все ответы на нажатия кнопок
    def callbackAnswers(self) -> tuple[CallbackAnswerRecord, ...]: ...
