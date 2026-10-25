"""DeliverNotification (J4): ровно одно сообщение, «доставлено» только после MAX, коды исходов."""

import asyncio
from datetime import UTC, datetime
from typing import Final

import pytest
import structlog

from bot.application.deliver_notification import DeliverNotification
from bot.application.reply_mapper import OpenAppMode, ReplyToMaxMapper
from bot.domain.notifications import DeliveryFailure, NotifyOutcome
from bot.infrastructure.fake_max import FakeMaxGateway, FakeMaxOptions
from bot.presentation.templates import RussianNotificationTexts
from contracts.notify import NotifyRequest
from services.bot.tests.fakes import (
    InMemoryCallbackStore,
    InMemoryNotificationLedger,
    notifyRequest,
)

# 18.10.2026 06:59 UTC = 09:59 в Москве — напоминание на 10:00 «сегодня»
NOW: Final = datetime(2026, 10, 18, 6, 59, tzinfo=UTC)
CHAT: Final = 500600700
NID: Final = "5c1e7a90-3f2b-4d6c-8e1a-9b0c2d3e4f5a"


# Примечание: стенд use case на фейках: MAX в памяти, учёт доставки и кнопки в памяти
class Rig:
    # Примечание: sendDelayS — «сеть MAX», чтобы два запроса пересеклись
    def __init__(
        self, ledger: InMemoryNotificationLedger | None = None, sendDelayS: float = 0.0
    ) -> None:
        self.gateway = FakeMaxGateway(FakeMaxOptions(sendDelayS=sendDelayS))
        self.ledger = ledger or InMemoryNotificationLedger()
        self.callbacks = InMemoryCallbackStore()
        self.focused: list[NotifyRequest] = []
        self.deliver = DeliverNotification(
            ledger=self.ledger,
            gateway=self.gateway,
            mapper=ReplyToMaxMapper(self.callbacks, mode=OpenAppMode.OPEN_APP, botUsername="bot"),
            texts=RussianNotificationTexts(lambda: NOW),
            focus=self,
        )

    # Примечание: FocusNotifier — запоминает, про какие уведомления сказали агенту
    def itemDelivered(self, request: NotifyRequest) -> None:
        self.focused.append(request)


# Примечание: доставка: текст и кнопки напоминания, «доставлено» с id сообщения MAX
async def test_delivered() -> None:
    rig = Rig()
    result = await rig.deliver(notifyRequest())
    assert result.outcome is NotifyOutcome.DELIVERED
    [sent] = rig.gateway.sentMessages()
    assert result.messageId == sent.messageId == rig.ledger.delivered[NID]
    assert (sent.chatId, sent.format) == (CHAT, "markdown")
    assert sent.text == "⏰ Позвонить Ивану\nСегодня, вс 18 октября, 10:00"
    buttons = sent.attachments[0]["payload"]["buttons"]  # type: ignore[index]
    assert [[b["text"] for b in row] for row in buttons] == [
        ["Выполнено", "Отложить на 1 ч"],
        ["В календаре"],
    ]
    stored = [rig.callbacks.saved[b["payload"]] for b in buttons[0]]
    assert [(s.action, s.args, s.chatId) for s in stored] == [
        ("complete", {"item_id": "7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90", "kind": "reminder"}, CHAT),
        (
            "snooze",
            {"item_id": "7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90", "kind": "reminder", "minutes": 60},
            CHAT,
        ),
    ]
    assert buttons[1][0]["payload"] == "item_7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90"
    assert rig.ledger.locks == {}


# Примечание: повтор после доставки — duplicate с тем же message_id, второго сообщения нет
async def test_duplicateAfterDelivery() -> None:
    rig = Rig()
    first = await rig.deliver(notifyRequest())
    again = await rig.deliver(notifyRequest(attempt=2))
    assert (again.outcome, again.messageId) == (NotifyOutcome.DUPLICATE, first.messageId)
    assert len(rig.gateway.sentMessages()) == 1


# Примечание: два одновременных запроса — одно сообщение: второй получает in_progress, а его
# повтор после доставки — duplicate
async def test_parallelSendsOnce() -> None:
    rig = Rig(sendDelayS=0.05)
    first, second = await asyncio.gather(
        rig.deliver(notifyRequest()), rig.deliver(notifyRequest(attempt=2))
    )
    assert {first.outcome, second.outcome} == {NotifyOutcome.DELIVERED, NotifyOutcome.IN_PROGRESS}
    assert len(rig.gateway.sentMessages()) == 1
    retry = await rig.deliver(notifyRequest(attempt=3))
    assert retry.outcome is NotifyOutcome.DUPLICATE


# Примечание: отказ MAX до отправки — «доставлено» не записано, «в работе» снято: повтор
# Scheduler'а после восстановления доставляет
@pytest.mark.parametrize(
    ("failure", "outcome"),
    [
        (DeliveryFailure.CHAT_NOT_FOUND, NotifyOutcome.CHAT_NOT_FOUND),
        (DeliveryFailure.RATE_LIMITED, NotifyOutcome.RATE_LIMITED),
        (DeliveryFailure.UNAVAILABLE, NotifyOutcome.UNAVAILABLE),
    ],
)
async def test_maxFailureNotMarked(failure: DeliveryFailure, outcome: NotifyOutcome) -> None:
    rig = Rig()
    rig.gateway.planFailure(CHAT, failure, 1)
    result = await rig.deliver(notifyRequest())
    assert result.outcome is outcome
    assert (rig.ledger.delivered, rig.ledger.locks) == ({}, {})
    assert rig.gateway.sentMessages() == ()
    retry = await rig.deliver(notifyRequest(attempt=2))
    assert retry.outcome is NotifyOutcome.DELIVERED


# Примечание: учёт доставки недоступен — не шлём вслепую: unavailable, сообщения нет
async def test_ledgerDownNotSent() -> None:
    rig = Rig(InMemoryNotificationLedger(failRead=True))
    assert (await rig.deliver(notifyRequest())).outcome is NotifyOutcome.UNAVAILABLE
    assert rig.gateway.sentMessages() == ()


# Примечание: сообщение ушло, а запись «доставлено» не удалась — всё равно delivered (в лог)
async def test_markFailureStillDelivered() -> None:
    rig = Rig(InMemoryNotificationLedger(failMark=True))
    with structlog.testing.capture_logs() as logs:
        result = await rig.deliver(notifyRequest())
    assert result.outcome is NotifyOutcome.DELIVERED
    assert any(e["event"] == "notification_mark_failed" for e in logs)
    assert "Позвонить" not in str(logs)


# Примечание: встреча — без «Выполнено», время со концом; срок задачи на весь день — без времени
async def test_eventAndTaskButtons() -> None:
    rig = Rig()
    event = notifyRequest(
        kind="event",
        item={
            "kind": "event",
            "title": "Созвон",
            "starts_at": "2026-10-18T15:00:00+03:00",
            "ends_at": "2026-10-18T16:00:00+03:00",
            "remind_at": "2026-10-18T14:45:00+03:00",
        },
    )
    await rig.deliver(event)
    task = notifyRequest(
        notification_id="6d2f8b01-4a3c-4e7d-9f2b-0c1d2e3f4a5b",
        kind="task",
        item={
            "kind": "task",
            "title": "Сдать отчёт",
            "starts_at": "2026-10-19T00:00:00+03:00",
            "all_day": True,
            "remind_at": None,
        },
    )
    await rig.deliver(task)
    meeting, deadline = rig.gateway.sentMessages()
    assert meeting.text == "Скоро встреча: Созвон\nСегодня, вс 18 октября, 15:00–16:00"
    labels = [b["text"] for b in meeting.attachments[0]["payload"]["buttons"][0]]  # type: ignore[index]
    assert labels == ["Отложить на 1 ч"]
    assert deadline.text == "Срок задачи: Сдать отчёт\nЗавтра, пн 19 октября"


# Примечание: S3 — запись в фокус агента ставится только после первой доставки: не при
# duplicate, не при отказе MAX
async def test_focusOnlyAfterDelivery() -> None:
    rig = Rig()
    rig.gateway.planFailure(CHAT, DeliveryFailure.UNAVAILABLE, 1)
    await rig.deliver(notifyRequest())
    assert rig.focused == []
    await rig.deliver(notifyRequest(attempt=2))
    await rig.deliver(notifyRequest(attempt=3))
    assert [str(r.notificationId) for r in rig.focused] == [NID]
