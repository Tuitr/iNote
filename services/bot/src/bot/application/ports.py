from typing import Protocol

from bot.domain.messages import MessageId, OutgoingMessage
from bot.domain.updates import MaxUserId, UpdateBatch, UpdateId


# Примечание: порт к MAX; реализации — FakeMaxGateway и (позже) реальный адаптер
class MaxGateway(Protocol):
    # Примечание: long polling: ждёт апдейты до timeoutS; marker=None — «ещё не полученные»
    async def fetchUpdates(self, marker: int | None, timeoutS: int) -> UpdateBatch: ...

    # Примечание: отправить сообщение, вернуть id созданного сообщения
    async def sendMessage(self, message: OutgoingMessage) -> MessageId: ...

    # Примечание: ответ на нажатие кнопки (POST /answers): всплывающее уведомление
    async def answerCallback(self, callbackId: str, notification: str | None) -> None: ...


# Примечание: дедуп апдейтов — MAX может прислать одно событие дважды
class UpdateDeduplicator(Protocol):
    # Примечание: True — видим впервые и обрабатываем; False — дубль
    async def isFirstSeen(self, updateId: UpdateId) -> bool: ...


# Примечание: ограничение частоты сообщений пользователя (20 в минуту)
class RateLimiter(Protocol):
    # Примечание: True — можно обработать, False — превышен лимит окна
    async def allow(self, maxUserId: MaxUserId) -> bool: ...


# Примечание: тексты ответов; живут в presentation/templates.py, а не в use case
class BotTexts(Protocol):
    # Примечание: эхо текста пользователя
    def echo(self, text: str) -> str: ...

    # Примечание: эхо имени команды
    def command(self, name: str) -> str: ...

    # Примечание: заглушка голосовых до J1
    def voiceNotSupportedYet(self) -> str: ...

    # Примечание: уведомление на нажатие кнопки
    def callbackAck(self, payload: str | None) -> str: ...

    # Примечание: приветствие на «Начать»
    def greeting(self) -> str: ...

    # Примечание: превышен лимит частоты
    def tooOften(self) -> str: ...
