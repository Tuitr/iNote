import asyncio
import itertools
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from bot.application.dev_console import CallbackAnswerRecord, SentMessageRecord, VoiceFileRecord
from bot.domain.messages import MessageId, OutgoingMessage
from bot.domain.updates import MaxChatId, UpdateBatch
from bot.infrastructure.max_mapper import mapRawUpdate
from bot.infrastructure.max_outgoing import attachments
from common.logging import getLogger

_log = getLogger(__name__)


# Фейковое голосовое = «OggS» + текст в UTF-8: ASR-заглушка возвращает этот текст (deploy/stub/asr)
FAKE_AUDIO_MAGIC: Final = b"OggS"
FILES_PATH: Final = "/dev/max/files"


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

    # Примечание: подложить сырой апдейт MAX; возвращает его позицию (marker = позиция + 1)
    async def pushRaw(self, raw: Mapping[str, object]) -> int:
        async with self._changed:
            self._raw.append(dict(raw))
            self._changed.notify_all()
            return len(self._raw) - 1

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
        messageId = f"fake-mid-{next(self._messageIds)}"
        # Вложения — ровно то, что ушло бы в POST /messages: так тесты видят формат MAX
        self._sent.append(
            SentMessageRecord(
                messageId=messageId,
                chatId=message.chatId,
                text=message.text,
                format=message.format.value,
                attachments=attachments(message),
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
