"""J4: Scheduler просит доставить уведомление — бот шлёт сообщение в MAX ровно один раз.

1. Уже доставлено (va:v1:bot:notif:{id}, 24 ч) → duplicate без отправки.
2. «В работе» — короткий ключ SET NX: параллельный повтор того же уведомления не шлёт второе
   сообщение, а получает in_progress (Scheduler повторит позже).
3. Отправка в MAX; «доставлено» с message_id пишется только после ответа MAX — иначе повтор
   Scheduler'а после сбоя потерял бы уведомление.
4. Отказ MAX → причина для кода ответа: чат не найден — без повторов, 429 и 5xx — повторить.
"""

from typing import Final

from bot.application.ports import (
    MaxGateway,
    NotificationLedger,
    NotificationTexts,
)
from bot.application.reply_mapper import ReplyToMaxMapper
from bot.domain.messages import MessageId
from bot.domain.notifications import (
    DeliveryFailure,
    DeliveryResult,
    MaxDeliveryError,
    NotifyOutcome,
)
from common.logging import getLogger
from common.trace import currentTraceId, newTraceId
from contracts.notify import NotifyRequest
from contracts.reply import (
    ActionButton,
    OpenAppButton,
    Reply,
    ReplyButton,
    ReplyFormat,
    ReplyState,
)
from contracts.start_param import StartParam

# «В работе» держим дольше одной отправки в MAX (10 с × 3 попытки), но меньше паузы Scheduler'а
DEFAULT_CLAIM_TTL_S: Final = 60
SNOOZE_MINUTES: Final = 60
SNOOZE_TOMORROW: Final = "tomorrow"

_FAILURES: Final = {
    DeliveryFailure.CHAT_NOT_FOUND: NotifyOutcome.CHAT_NOT_FOUND,
    DeliveryFailure.RATE_LIMITED: NotifyOutcome.RATE_LIMITED,
    DeliveryFailure.UNAVAILABLE: NotifyOutcome.UNAVAILABLE,
}

_log = getLogger(__name__)


# Примечание: доставка одного уведомления; никогда не бросает — исход для кода ответа J4
class DeliverNotification:
    # Примечание: mapper — тот же, что для ответов агента: callback-ключи, open_app, лимиты MAX
    def __init__(
        self,
        *,
        ledger: NotificationLedger,
        gateway: MaxGateway,
        mapper: ReplyToMaxMapper,
        texts: NotificationTexts,
        claimTtlS: int = DEFAULT_CLAIM_TTL_S,
    ) -> None:
        self._ledger = ledger
        self._gateway = gateway
        self._mapper = mapper
        self._texts = texts
        self._claimTtlS = claimTtlS

    # Примечание: в лог — id уведомления, kind, attempt и исход; текста и названия нет
    async def __call__(self, request: NotifyRequest) -> DeliveryResult:
        notificationId = str(request.notificationId)
        result = await self._deliver(request, notificationId)
        _log.info(
            "notification_handled",
            notification_id=notificationId,
            kind=request.kind,
            attempt=request.attempt,
            outcome=result.outcome.value,
            message_id=result.messageId,
        )
        return result

    # Примечание: дедуп и «в работе» → отправка → «доставлено»; «в работе» снимаем всегда
    async def _deliver(self, request: NotifyRequest, notificationId: str) -> DeliveryResult:
        claimed = await self._claim(notificationId)
        if isinstance(claimed, DeliveryResult):
            return claimed
        try:
            return await self._sendOnce(request, notificationId)
        finally:
            await self._release(notificationId, claimed)

    # Примечание: уже доставлено → duplicate; кто-то шлёт → in_progress; иначе токен «в работе»
    async def _claim(self, notificationId: str) -> DeliveryResult | str:
        try:
            delivered = await self._ledger.deliveredMessage(notificationId)
            if delivered is not None:
                return DeliveryResult(NotifyOutcome.DUPLICATE, delivered)
            token = await self._ledger.claim(notificationId, self._claimTtlS)
        except Exception:  # noqa: BLE001 — без учёта доставки шлём позже, а не вслепую
            _log.exception("notification_ledger_failed", notification_id=notificationId)
            return DeliveryResult(NotifyOutcome.UNAVAILABLE)
        return token if token is not None else DeliveryResult(NotifyOutcome.IN_PROGRESS)

    # Примечание: под «в работе»: повторная проверка (первый запрос мог успеть доставить между
    # проверкой и захватом), отправка, «доставлено» — только после ответа MAX
    async def _sendOnce(self, request: NotifyRequest, notificationId: str) -> DeliveryResult:
        try:
            delivered = await self._ledger.deliveredMessage(notificationId)
            if delivered is not None:
                return DeliveryResult(NotifyOutcome.DUPLICATE, delivered)
            messageId = await self._send(request)
        except MaxDeliveryError as exc:
            _log.warning(
                "notification_rejected", notification_id=notificationId, reason=exc.reason.value
            )
            return DeliveryResult(_FAILURES[exc.reason])
        except Exception:  # noqa: BLE001 — Scheduler повторит; «доставлено» не записано
            _log.exception("notification_send_failed", notification_id=notificationId)
            return DeliveryResult(NotifyOutcome.UNAVAILABLE)
        await self._markDelivered(notificationId, messageId)
        return DeliveryResult(NotifyOutcome.DELIVERED, messageId)

    # Примечание: текст и кнопки → сообщение MAX в чат пользователя
    async def _send(self, request: NotifyRequest) -> MessageId:
        reply = Reply(
            text=self._texts.notificationBody(request),
            format=ReplyFormat.MARKDOWN,
            state=ReplyState.ANSWER,
            buttons=self._buttons(request),
            traceId=currentTraceId() or newTraceId(),
        )
        message = await self._mapper.toMessage(reply, request.user.maxChatId)
        return await self._gateway.sendMessage(message)

    # Примечание: «Выполнено» (не у встречи), «Отложить на 1 ч», «Завтра» и запись в календаре
    def _buttons(self, request: NotifyRequest) -> list[list[ReplyButton]]:
        t = self._texts.notificationButtons()
        item = request.item
        base: dict[str, str | int | bool] = {"item_id": str(item.id), "kind": item.kind}
        actions: list[ReplyButton] = []
        if item.kind != "event":
            actions.append(ActionButton(text=t.done, action="complete", args=dict(base)))
        actions.append(
            ActionButton(
                text=t.snoozeHour, action="snooze", args={**base, "minutes": SNOOZE_MINUTES}
            )
        )
        actions.append(
            ActionButton(text=t.tomorrow, action="snooze", args={**base, "until": SNOOZE_TOMORROW})
        )
        app = OpenAppButton(text=t.openItem, startParam=str(StartParam.forItem(item.id)))
        return [actions, [app]]

    # Примечание: сообщение уже в MAX: сбой записи — только лог (повтор пришлёт дубль, но
    # пользователь уведомление получил; Scheduler получит delivered)
    async def _markDelivered(self, notificationId: str, messageId: MessageId) -> None:
        try:
            await self._ledger.markDelivered(notificationId, messageId)
        except Exception:  # noqa: BLE001
            _log.exception("notification_mark_failed", notification_id=notificationId)

    # Примечание: снять «в работе»; не вышло — ключ истечёт сам через claimTtlS
    async def _release(self, notificationId: str, token: str) -> None:
        try:
            await self._ledger.release(notificationId, token)
        except Exception:  # noqa: BLE001
            _log.warning("notification_release_failed", notification_id=notificationId)
