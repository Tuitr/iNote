import time
from enum import StrEnum
from typing import assert_never

from bot.application.ports import BotTexts, MaxGateway, RateLimiter, UpdateDeduplicator
from bot.domain.messages import OutgoingMessage, fitToLimit
from bot.domain.updates import IncomingUpdate, UpdateKind
from common.logging import getLogger

_log = getLogger(__name__)


# Примечание: итог обработки апдейта — для логов и тестов
class HandleOutcome(StrEnum):
    HANDLED = "handled"
    DUPLICATE = "duplicate"
    RATE_LIMITED = "rate_limited"
    NO_CHAT = "no_chat"


# Примечание: use case S0: дедуп → лимит → эхо по виду апдейта (агента и ASR ещё нет)
class HandleUpdate:
    # Примечание: все зависимости — порты, собираются в main.py
    def __init__(
        self,
        *,
        gateway: MaxGateway,
        deduplicator: UpdateDeduplicator,
        rateLimiter: RateLimiter,
        texts: BotTexts,
    ) -> None:
        self._gateway = gateway
        self._dedup = deduplicator
        self._limiter = rateLimiter
        self._texts = texts

    # Примечание: дубль не считается в лимит; в лог — вид и итог, без текста пользователя
    async def __call__(self, update: IncomingUpdate) -> HandleOutcome:
        started = time.perf_counter()
        if not await self._dedup.isFirstSeen(update.updateId):
            outcome = HandleOutcome.DUPLICATE
        elif not await self._limiter.allow(update.maxUserId):
            outcome = await self._replyTooOften(update)
        else:
            outcome = await self._dispatch(update)
        _log.info(
            "update_handled",
            update_id=update.updateId,
            kind=update.kind.value,
            outcome=outcome.value,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return outcome

    # Примечание: маршрутизация по виду; позже текст и команды уйдут агенту, голос — в ASR
    async def _dispatch(self, update: IncomingUpdate) -> HandleOutcome:
        if update.kind is UpdateKind.CALLBACK:
            await self._answerCallback(update, self._texts.callbackAck(update.callbackPayload))
            return HandleOutcome.HANDLED
        return await self._send(update, self._replyText(update))

    # Примечание: текст ответа по виду апдейта; новый вид без ветки — ошибка mypy (assert_never)
    def _replyText(self, update: IncomingUpdate) -> str:
        match update.kind:
            case UpdateKind.TEXT:
                return self._texts.echo(update.text or "")
            case UpdateKind.COMMAND:
                return self._texts.command(update.commandName or "")
            case UpdateKind.VOICE:
                return self._texts.voiceNotSupportedYet()
            case UpdateKind.BOT_STARTED:
                return self._texts.greeting()
            case UpdateKind.CALLBACK:
                return self._texts.callbackAck(update.callbackPayload)
            case _:
                assert_never(update.kind)

    # Примечание: на превышение лимита — короткий ответ тем же каналом, что и запрос
    async def _replyTooOften(self, update: IncomingUpdate) -> HandleOutcome:
        if update.kind is UpdateKind.CALLBACK:
            await self._answerCallback(update, self._texts.tooOften())
        else:
            await self._send(update, self._texts.tooOften())
        return HandleOutcome.RATE_LIMITED

    # Примечание: ответ в чат апдейта; без чата отвечать некуда — фиксируем в логе
    async def _send(self, update: IncomingUpdate, text: str) -> HandleOutcome:
        if update.maxChatId is None:
            _log.warning("update_without_chat", update_id=update.updateId)
            return HandleOutcome.NO_CHAT
        await self._gateway.sendMessage(OutgoingMessage(update.maxChatId, fitToLimit(text)))
        return HandleOutcome.HANDLED

    # Примечание: у callback-апдейта всегда есть callback_id (гарантирует маппер)
    async def _answerCallback(self, update: IncomingUpdate, notification: str) -> None:
        if update.callbackId is None:
            msg = f"callback без callback_id: {update.updateId}"
            raise ValueError(msg)
        await self._gateway.answerCallback(update.callbackId, notification)
