"""Доставка уведомления J4: почему MAX не принял сообщение и чем закончилась попытка."""

from dataclasses import dataclass
from enum import StrEnum

from bot.domain.messages import MessageId


# Примечание: отказ MAX при отправке; от причины зависит, повторит ли Scheduler (J4)
class DeliveryFailure(StrEnum):
    # чат не найден или бот заблокирован — повтор бесполезен
    CHAT_NOT_FOUND = "chat_not_found"
    # 429 от MAX — повторить позже
    RATE_LIMITED = "rate_limited"
    # таймаут, сеть или 5xx MAX — повторить
    UNAVAILABLE = "unavailable"


# Примечание: MAX не принял сообщение; бросает MaxGateway.sendMessage
class MaxDeliveryError(Exception):
    # Примечание: reason — для ответа Scheduler'у; detail — в лог (без текста сообщения)
    def __init__(self, reason: DeliveryFailure, detail: str = "") -> None:
        super().__init__(f"{reason.value}: {detail}" if detail else reason.value)
        self.reason = reason


# Примечание: исход приёма уведомления — по нему роут выбирает код ответа J4
class NotifyOutcome(StrEnum):
    DELIVERED = "delivered"
    DUPLICATE = "duplicate"
    # то же уведомление сейчас отправляется другим запросом — Scheduler повторит позже
    IN_PROGRESS = "in_progress"
    CHAT_NOT_FOUND = "chat_not_found"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"


# Примечание: итог: исход и id сообщения MAX (у delivered и duplicate)
@dataclass(frozen=True)
class DeliveryResult:
    outcome: NotifyOutcome
    messageId: MessageId | None = None
