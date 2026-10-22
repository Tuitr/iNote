from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol


# Примечание: запись журнала отправленного сообщения (фейковый MAX)
@dataclass(frozen=True)
class SentMessageRecord:
    messageId: str
    chatId: int
    text: str
    format: str
    # вложения NewMessageBody как их получил бы MAX (клавиатура inline_keyboard)
    attachments: list[dict[str, object]] = field(default_factory=list)


# Примечание: фейковое голосовое: байты и content-type для скачивания по ссылке
@dataclass(frozen=True)
class VoiceFileRecord:
    content: bytes
    mime: str = "audio/ogg"


# Примечание: запись журнала ответа на нажатие кнопки
@dataclass(frozen=True)
class CallbackAnswerRecord:
    callbackId: str
    notification: str | None


# Примечание: подписка webhook фейкового MAX; секрет наружу не показываем — только факт
@dataclass(frozen=True)
class SubscriptionRecord:
    url: str
    hasSecret: bool
    updateTypes: tuple[str, ...]


# Примечание: dev-порт фейкового MAX для /dev/max/*; в режиме real его нет вовсе
class DevMaxConsole(Protocol):
    # Примечание: подложить сырой апдейт MAX, вернуть его позицию в очереди
    async def pushRaw(self, raw: Mapping[str, object]) -> int: ...

    # Примечание: всё, что бот отправил
    def sentMessages(self) -> tuple[SentMessageRecord, ...]: ...

    # Примечание: все ответы на нажатия кнопок
    def callbackAnswers(self) -> tuple[CallbackAnswerRecord, ...]: ...

    # Примечание: чаты, куда уходило «печатает…», по порядку
    def typingActions(self) -> tuple[int, ...]: ...

    # Примечание: голосовое от пользователя — файл в памяти и апдейт со ссылкой на него
    async def pushVoice(self, maxUserId: int, maxChatId: int, text: str) -> int: ...

    # Примечание: файл голосового для скачивания ботом
    def voiceFile(self, fileId: str) -> VoiceFileRecord | None: ...

    # Примечание: подписки webhook, которые оформил бот (по порядку вызовов)
    def subscriptions(self) -> tuple[SubscriptionRecord, ...]: ...
