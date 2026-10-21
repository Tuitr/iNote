import asyncio
import itertools
from collections.abc import Mapping
from dataclasses import dataclass

from bot.application.dev_console import CallbackAnswerRecord, SentMessageRecord
from bot.domain.messages import MessageId, OutgoingMessage
from bot.domain.updates import UpdateBatch
from bot.infrastructure.max_mapper import mapRawUpdate
from common.logging import getLogger

_log = getLogger(__name__)


# Примечание: настройки фейка: задержка отправки имитирует сеть MAX (для lifecycle-тестов)
@dataclass(frozen=True)
class FakeMaxOptions:
    sendDelayS: float = 0.0


# Примечание: MAX в памяти (BOT_MAX_MODE=fake): очередь сырых апдейтов с marker и журнал
class FakeMaxGateway:
    # Примечание: состояние только в процессе — перезапуск начинает с чистого листа
    def __init__(self, options: FakeMaxOptions | None = None) -> None:
        self._options = options or FakeMaxOptions()
        self._raw: list[dict[str, object]] = []
        self._delivered = 0
        self._changed = asyncio.Condition()
        self._sent: list[SentMessageRecord] = []
        self._answers: list[CallbackAnswerRecord] = []
        self._messageIds = itertools.count(1)

    # Примечание: подложить сырой апдейт MAX; возвращает его позицию (marker = позиция + 1)
    async def pushRaw(self, raw: Mapping[str, object]) -> int:
        async with self._changed:
            self._raw.append(dict(raw))
            self._changed.notify_all()
            return len(self._raw) - 1

    # Примечание: как GET /updates: marker=None — с первого ещё не выданного; старый marker — повтор
    async def fetchUpdates(self, marker: int | None, timeoutS: int) -> UpdateBatch:
        start = self._delivered if marker is None else marker
        async with self._changed:
            # timeout=0 — отдать накопленное сразу, без ожидания (как в MAX)
            if len(self._raw) <= start:
                try:
                    await asyncio.wait_for(
                        self._changed.wait_for(lambda: len(self._raw) > start), timeout=timeoutS
                    )
                except TimeoutError:
                    return UpdateBatch(updates=(), marker=start)
            raws = self._raw[start:]
            nextMarker = len(self._raw)
            self._delivered = max(self._delivered, nextMarker)
        mapped = (mapRawUpdate(raw) for raw in raws)
        return UpdateBatch(updates=tuple(u for u in mapped if u is not None), marker=nextMarker)

    # Примечание: «отправка» — запись в журнал; текст в лог не пишем
    async def sendMessage(self, message: OutgoingMessage) -> MessageId:
        if self._options.sendDelayS > 0:
            await asyncio.sleep(self._options.sendDelayS)
        messageId = f"fake-mid-{next(self._messageIds)}"
        self._sent.append(
            SentMessageRecord(
                messageId=messageId,
                chatId=message.chatId,
                text=message.text,
                format=message.format.value,
            )
        )
        _log.info("max_message_sent", message_id=messageId, fake=True)
        return messageId

    # Примечание: ответ на callback — тоже в журнал
    async def answerCallback(self, callbackId: str, notification: str | None) -> None:
        if self._options.sendDelayS > 0:
            await asyncio.sleep(self._options.sendDelayS)
        self._answers.append(CallbackAnswerRecord(callbackId=callbackId, notification=notification))
        _log.info("max_callback_answered", fake=True)

    # Примечание: журнал отправленных сообщений (для /dev/max/sent и тестов)
    def sentMessages(self) -> tuple[SentMessageRecord, ...]:
        return tuple(self._sent)

    # Примечание: журнал ответов на нажатия кнопок
    def callbackAnswers(self) -> tuple[CallbackAnswerRecord, ...]:
        return tuple(self._answers)
