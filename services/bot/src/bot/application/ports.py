from dataclasses import dataclass
from typing import Protocol

from bot.domain.messages import MessageId, OutgoingMessage
from bot.domain.updates import MaxChatId, MaxUserId, UpdateBatch, UpdateId, VoiceRef
from bot.domain.voice import AudioFile, Transcript, VoiceFailureReason
from contracts.notify import NotifyRequest
from contracts.reply import ActionRequest, ArgValue, MessageRequest, Reply


# Примечание: порт к MAX; реализации — FakeMaxGateway и (позже) реальный адаптер
class MaxGateway(Protocol):
    # Примечание: long polling: ждёт апдейты до timeoutS; marker=None — «ещё не полученные»
    async def fetchUpdates(self, marker: int | None, timeoutS: int) -> UpdateBatch: ...

    # Примечание: отправить сообщение, вернуть id созданного сообщения; отказ MAX (чат не найден,
    # 429, сеть или 5xx) — MaxDeliveryError с причиной
    async def sendMessage(self, message: OutgoingMessage) -> MessageId: ...

    # Примечание: ответ на нажатие кнопки (POST /answers): всплывающее уведомление
    async def answerCallback(self, callbackId: str, notification: str | None) -> None: ...

    # Примечание: «печатает…» в чате (POST /chats/{chat_id}/actions, typing_on)
    async def sendTyping(self, chatId: MaxChatId) -> None: ...

    # Примечание: подписка на webhook (POST /subscriptions) с секретом для X-Max-Bot-Api-Secret;
    # вызывается при каждом старте в режиме webhook — MAX отписывает бота после 8 ч без 200
    async def ensureSubscription(self, url: str, secret: str) -> None: ...


# Примечание: скачать голосовое сразу — ссылка MAX живёт около суток; ошибка — VoiceFailureError
class AudioDownloader(Protocol):
    # Примечание: байты голосового в пределах лимита размера
    async def download(self, voice: VoiceRef) -> AudioFile: ...


# Примечание: распознавание речи (J1); ошибка — VoiceFailureError с причиной для ответа бота
class AsrGateway(Protocol):
    # Примечание: аудио → текст
    async def transcribe(self, audio: AudioFile) -> Transcript: ...


# Примечание: агент (API агента из contracts/reply.py); сбой — AppError с кодом стандарта
class AgentGateway(Protocol):
    # Примечание: текст пользователя → POST /v1/messages
    async def sendMessage(self, request: MessageRequest) -> Reply: ...

    # Примечание: кнопка или команда без LLM → POST /v1/actions
    async def sendAction(self, request: ActionRequest) -> Reply: ...


# Примечание: «эта запись теперь в фокусе» — агенту после доставленного уведомления (POST
# /v1/focus); не ждёт ответа и не бросает: уведомление уже у пользователя
class FocusNotifier(Protocol):
    # Примечание: ставит вызов в фон
    def itemDelivered(self, request: NotifyRequest) -> None: ...


# Примечание: что лежит за callback-кнопкой: действие, аргументы для /v1/actions и чат, куда
# ушла кнопка (нажатие из другого чата — например, пересланное сообщение — не принимаем)
@dataclass(frozen=True)
class StoredAction:
    action: str
    args: dict[str, ArgValue]
    chatId: MaxChatId | None = None
    # подпись кнопки — реплика пользователя в истории диалога (ActionRequest.label)
    label: str | None = None


# Примечание: payload кнопки MAX — короткий ключ; само действие — в Redis на 24 ч
class CallbackStore(Protocol):
    # Примечание: сохранить действие, вернуть ключ для payload кнопки
    async def save(self, action: StoredAction) -> str: ...

    # Примечание: действие по ключу; None — ключ устарел, чужой или не наш формат
    async def load(self, key: str) -> StoredAction | None: ...


# Примечание: дедуп апдейтов — MAX может прислать одно событие дважды
class UpdateDeduplicator(Protocol):
    # Примечание: True — видим впервые и обрабатываем; False — дубль
    async def isFirstSeen(self, updateId: UpdateId) -> bool: ...


# Примечание: решение лимитера; notify — первое превышение в окне («Слишком часто» один раз)
@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    notify: bool = False


# Примечание: ограничение частоты сообщений пользователя (20 в минуту)
class RateLimiter(Protocol):
    # Примечание: можно ли обработать апдейт и надо ли предупредить пользователя
    async def check(self, maxUserId: MaxUserId) -> RateDecision: ...


# Примечание: где хранится marker long polling между перезапусками
class MarkerStore(Protocol):
    # Примечание: последний зафиксированный marker или None
    async def load(self) -> int | None: ...

    # Примечание: зафиксировать marker после обработки страницы
    async def save(self, marker: int) -> None: ...


# Примечание: тексты бота; живут в presentation/templates.py, а не в use case
class BotTexts(Protocol):
    # Примечание: голосовое не распознано
    def voiceFailed(self, reason: VoiceFailureReason) -> str: ...

    # Примечание: превышен лимит частоты
    def tooOften(self) -> str: ...

    # Примечание: агент не ответил
    def agentFailed(self) -> str: ...

    # Примечание: сбой самого бота (Redis недоступен и т. п.)
    def botFailed(self) -> str: ...

    # Примечание: кнопка из старого сообщения — ключа в Redis уже нет
    def buttonExpired(self) -> str: ...

    # Примечание: неизвестная команда — подсказка со списком команд
    def unknownCommand(self, name: str) -> str: ...


# Примечание: учёт доставки уведомлений J4: «доставлено» на 24 ч и короткий ключ «в работе»
class NotificationLedger(Protocol):
    # Примечание: id сообщения MAX, если уведомление уже доставлено; None — ещё нет
    async def deliveredMessage(self, notificationId: str) -> MessageId | None: ...

    # Примечание: взять «в работе» (SET NX с TTL); токен владельца или None — уже кто-то шлёт
    async def claim(self, notificationId: str, ttlS: int) -> str | None: ...

    # Примечание: записать «доставлено» с id сообщения — только после ответа MAX
    async def markDelivered(self, notificationId: str, messageId: MessageId) -> None: ...

    # Примечание: снять «в работе», если ключ всё ещё наш
    async def release(self, notificationId: str, token: str) -> None: ...


# Примечание: подписи кнопок уведомления: выполнено, отложить, в календаре
@dataclass(frozen=True)
class NotificationButtonTexts:
    done: str
    snoozeHour: str
    openItem: str


# Примечание: тексты уведомлений — в шаблонах бота: Scheduler формата MAX не знает
class NotificationTexts(Protocol):
    # Примечание: текст по kind в поясе пользователя (markdown, название экранировано)
    def notificationBody(self, request: NotifyRequest) -> str: ...

    # Примечание: подписи кнопок уведомления
    def notificationButtons(self) -> NotificationButtonTexts: ...
