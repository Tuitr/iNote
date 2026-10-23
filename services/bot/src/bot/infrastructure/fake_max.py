import asyncio
import itertools
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import httpx

from bot.application.dev_console import (
    CallbackAnswerRecord,
    SentMessageRecord,
    SubscriptionRecord,
    VoiceFileRecord,
)
from bot.domain.messages import MessageId, OutgoingMessage
from bot.domain.notifications import DeliveryFailure, MaxDeliveryError
from bot.domain.updates import WEBHOOK_SECRET_HEADER, MaxChatId, UpdateBatch
from bot.infrastructure.max_mapper import mapRawUpdate
from bot.infrastructure.max_outgoing import attachments
from common.lifecycle import TaskRegistry
from common.logging import getLogger
from common.trace import currentTraceId

_log = getLogger(__name__)


# Фейковое голосовое = «OggS» + текст в UTF-8: ASR-заглушка возвращает этот текст (deploy/stub/asr)
FAKE_AUDIO_MAGIC: Final = b"OggS"
FILES_PATH: Final = "/dev/max/files"
# Типы апдейтов, на которые подписывается бот (как в схеме MAX)
UPDATE_TYPES: Final = ("message_created", "message_callback", "bot_started")
WEBHOOK_TIMEOUT_S: Final = 30.0


# Примечание: настройки фейка: sendDelayS — сеть MAX; filesBaseUrl — откуда бот скачает голосовое
@dataclass(frozen=True)
class FakeMaxOptions:
    sendDelayS: float = 0.0
    filesBaseUrl: str = "http://127.0.0.1:8000"


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
        self._typing: list[MaxChatId] = []
        self._files: dict[str, VoiceFileRecord] = {}
        self._messageIds = itertools.count(1)
        self._subscriptions: list[SubscriptionRecord] = []
        self._secret = ""
        self._webhook: tuple[httpx.AsyncClient, TaskRegistry] | None = None
        # доставка по webhook — по одной и в порядке подкладывания (asyncio.Lock — FIFO)
        self._delivery = asyncio.Lock()
        # отказы MAX на отправку в чат: chat_id → (причина, сколько раз; -1 — всегда)
        self._failures: dict[MaxChatId, tuple[DeliveryFailure, int]] = {}

    # Примечание: сымитировать отказ MAX при отправке в чат (/dev/max/failures): чат не найден,
    # 429 или 5xx/таймаут; times — сколько отправок подряд, -1 — пока не сбросят (times=0)
    def planFailure(self, chatId: MaxChatId, reason: DeliveryFailure, times: int) -> None:
        if times == 0:
            self._failures.pop(chatId, None)
            return
        self._failures[chatId] = (reason, times)

    # Примечание: режим webhook: после подписки апдейты не копятся для GET /updates, а уходят
    # POST-ом на адрес подписки с секретом — как у настоящего MAX; client — HTTP к самому боту
    def attachWebhook(self, client: httpx.AsyncClient, tasks: TaskRegistry) -> None:
        self._webhook = (client, tasks)

    # Примечание: POST /subscriptions: запоминаем адрес и секрет; повтор при рестарте — норма
    async def ensureSubscription(self, url: str, secret: str) -> None:
        self._subscriptions.append(
            SubscriptionRecord(url=url, hasSecret=bool(secret), updateTypes=UPDATE_TYPES)
        )
        self._secret = secret
        _log.info("max_subscribed", fake=True, update_types=list(UPDATE_TYPES))

    # Примечание: подписки бота (для /dev/max/subscriptions и тестов)
    def subscriptions(self) -> tuple[SubscriptionRecord, ...]:
        return tuple(self._subscriptions)

    # Примечание: подложить сырой апдейт MAX; возвращает его позицию (marker = позиция + 1)
    async def pushRaw(self, raw: Mapping[str, object]) -> int:
        if self._webhook is not None and self._subscriptions:
            client, tasks = self._webhook
            _ = tasks.spawn(self._deliver(client, dict(raw)), name="fake-max-webhook")
        async with self._changed:
            self._raw.append(dict(raw))
            self._changed.notify_all()
            return len(self._raw) - 1

    # Примечание: доставка одного апдейта на webhook; не 200 — в лог (MAX повторил бы позже)
    async def _deliver(self, client: httpx.AsyncClient, raw: dict[str, object]) -> None:
        url = self._subscriptions[-1].url
        async with self._delivery:
            try:
                response = await client.post(
                    url,
                    json=raw,
                    headers={WEBHOOK_SECRET_HEADER: self._secret},
                    timeout=WEBHOOK_TIMEOUT_S,
                )
            except httpx.HTTPError as exc:
                _log.warning("fake_webhook_failed", error=type(exc).__name__)
                return
        if response.status_code != httpx.codes.OK:
            _log.warning("fake_webhook_rejected", status=response.status_code)

    # Примечание: голосовое от пользователя: файл в памяти + апдейт со ссылкой на него (как в MAX)
    async def pushVoice(self, maxUserId: int, maxChatId: int, text: str) -> int:
        fileId = uuid.uuid4().hex
        self._files[fileId] = VoiceFileRecord(FAKE_AUDIO_MAGIC + text.encode())
        now = int(time.time() * 1000)
        url = f"{self._options.filesBaseUrl.rstrip('/')}{FILES_PATH}/{fileId}"
        raw: dict[str, object] = {
            "update_type": "message_created",
            "timestamp": now,
            "message": {
                "sender": {"user_id": maxUserId, "first_name": "Dev", "is_bot": False},
                "recipient": {"chat_id": maxChatId, "chat_type": "dialog"},
                "timestamp": now,
                "body": {
                    "mid": f"mid.fake.voice.{fileId}",
                    "attachments": [{"type": "audio", "payload": {"url": url, "token": fileId}}],
                },
            },
            "user_locale": "ru",
        }
        return await self.pushRaw(raw)

    # Примечание: файл голосового по id; None — нет такого (404 в /dev/max/files)
    def voiceFile(self, fileId: str) -> VoiceFileRecord | None:
        return self._files.get(fileId)

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
        self._failIfPlanned(message.chatId)
        messageId = f"fake-mid-{next(self._messageIds)}"
        # Вложения — ровно то, что ушло бы в POST /messages: так тесты видят формат MAX
        self._sent.append(
            SentMessageRecord(
                messageId=messageId,
                chatId=message.chatId,
                text=message.text,
                format=message.format.value,
                attachments=attachments(message),
                traceId=currentTraceId(),
            )
        )
        _log.info("max_message_sent", message_id=messageId, fake=True)
        return messageId

    # Примечание: запланированный отказ для чата — MaxDeliveryError, как у настоящего адаптера
    def _failIfPlanned(self, chatId: MaxChatId) -> None:
        planned = self._failures.get(chatId)
        if planned is None:
            return
        reason, times = planned
        if times > 0:
            if times == 1:
                del self._failures[chatId]
            else:
                self._failures[chatId] = (reason, times - 1)
        _log.info("max_message_rejected", fake=True, reason=reason.value)
        raise MaxDeliveryError(reason, "fake")

    # Примечание: ответ на callback — тоже в журнал
    async def answerCallback(self, callbackId: str, notification: str | None) -> None:
        if self._options.sendDelayS > 0:
            await asyncio.sleep(self._options.sendDelayS)
        self._answers.append(CallbackAnswerRecord(callbackId=callbackId, notification=notification))
        _log.info("max_callback_answered", fake=True)

    # Примечание: «печатает…» — запись в журнал
    async def sendTyping(self, chatId: MaxChatId) -> None:
        self._typing.append(chatId)

    # Примечание: чаты, куда уходило «печатает…»
    def typingActions(self) -> tuple[MaxChatId, ...]:
        return tuple(self._typing)

    # Примечание: журнал отправленных сообщений (для /dev/max/sent и тестов)
    def sentMessages(self) -> tuple[SentMessageRecord, ...]:
        return tuple(self._sent)

    # Примечание: журнал ответов на нажатия кнопок
    def callbackAnswers(self) -> tuple[CallbackAnswerRecord, ...]:
        return tuple(self._answers)
