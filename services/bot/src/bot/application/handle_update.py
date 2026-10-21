import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, assert_never

from bot.application.ports import (
    AgentGateway,
    AsrGateway,
    AudioDownloader,
    BotTexts,
    CallbackStore,
    MaxGateway,
    RateLimiter,
    UpdateDeduplicator,
)
from bot.application.reply_mapper import ReplyToMaxMapper
from bot.application.typing import TypingIndicator
from bot.domain.messages import OutgoingMessage, fitToLimit
from bot.domain.updates import IncomingUpdate, MaxChatId, UpdateKind, VoiceRef
from bot.domain.voice import Transcript, VoiceFailureError
from common.errors import AppError, ErrorCode
from common.logging import getLogger
from common.trace import shortTraceId
from contracts.reply import (
    REPLY_TEXT_MAX_LENGTH,
    ActionRequest,
    ArgValue,
    MessageRequest,
    Reply,
)

# Команды без LLM → POST /v1/actions (обработку действий агент получает в S2)
COMMAND_ACTIONS: Final[dict[str, tuple[str, dict[str, ArgValue]]]] = {
    "today": ("list_period", {"period": "today"}),
    "tomorrow": ("list_period", {"period": "tomorrow"}),
    "week": ("list_period", {"period": "week"}),
}
LOCAL_COMMANDS: Final = frozenset({"start", "help"})

_log = getLogger(__name__)


# Примечание: итог обработки апдейта — для логов и тестов
class HandleOutcome(StrEnum):
    HANDLED = "handled"
    DUPLICATE = "duplicate"
    RATE_LIMITED = "rate_limited"
    NO_CHAT = "no_chat"
    AGENT_FAILED = "agent_failed"
    VOICE_FAILED = "voice_failed"
    BUTTON_EXPIRED = "button_expired"


# Примечание: зависимости use case — порты и помощники, собираются в main.py
@dataclass(frozen=True)
class BotDeps:
    gateway: MaxGateway
    deduplicator: UpdateDeduplicator
    rateLimiter: RateLimiter
    texts: BotTexts
    agent: AgentGateway
    callbacks: CallbackStore
    mapper: ReplyToMaxMapper
    typing: TypingIndicator
    downloader: AudioDownloader
    asr: AsrGateway


# Примечание: дедуп → лимит → агент (текст, команды, кнопки) → ответ в формате MAX
class HandleUpdate:
    # Примечание: бизнес-логики здесь нет — её делает агент (правило 7)
    def __init__(self, deps: BotDeps) -> None:
        self._d = deps

    # Примечание: дубль не считается в лимит; в лог — вид и итог, без текста пользователя
    async def __call__(self, update: IncomingUpdate) -> HandleOutcome:
        started = time.perf_counter()
        if not await self._d.deduplicator.isFirstSeen(update.updateId):
            outcome = HandleOutcome.DUPLICATE
        elif not (decision := await self._d.rateLimiter.check(update.maxUserId)).allowed:
            outcome = await self._replyTooOften(update, notify=decision.notify)
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

    # Примечание: маршрутизация по виду апдейта; голосовое — заглушка до J1 (ASR)
    async def _dispatch(self, update: IncomingUpdate) -> HandleOutcome:
        if update.kind is UpdateKind.CALLBACK:
            return await self._onCallback(update)
        chatId = update.maxChatId
        if chatId is None:
            _log.warning("update_without_chat", update_id=update.updateId)
            return HandleOutcome.NO_CHAT
        match update.kind:
            case UpdateKind.TEXT:
                return await self._onText(update, chatId)
            case UpdateKind.COMMAND:
                return await self._onCommand(update, chatId)
            case UpdateKind.VOICE:
                return await self._onVoice(update, chatId)
            case UpdateKind.BOT_STARTED | UpdateKind.CALLBACK:
                return await self._sendText(chatId, self._d.texts.greeting())
            case _:
                assert_never(update.kind)

    # Примечание: текст → /v1/messages; имя и язык — для J8 и ответа
    async def _onText(self, update: IncomingUpdate, chatId: MaxChatId) -> HandleOutcome:
        request = MessageRequest(
            maxUserId=update.maxUserId,
            maxChatId=chatId,
            text=fitToLimit(update.text or "", REPLY_TEXT_MAX_LENGTH),
            source="text",
            locale=update.locale,
            name=update.userName,
        )
        return await self._askAgent(chatId, lambda: self._d.agent.sendMessage(request))

    # Примечание: голос: «печатает…» сразу и до ответа — скачать, распознать, отдать агенту
    async def _onVoice(self, update: IncomingUpdate, chatId: MaxChatId) -> HandleOutcome:
        if update.voice is None:
            msg = f"голосовое без вложения: {update.updateId}"
            raise ValueError(msg)
        async with self._d.typing.during(chatId):
            try:
                transcript = await self._transcribe(update.voice)
            except VoiceFailureError as exc:
                _log.info("voice_failed", reason=exc.reason.value, error_code=exc.code)
                text = self._d.texts.voiceFailed(exc.reason, shortTraceId())
                await self._sendText(chatId, text)
                return HandleOutcome.VOICE_FAILED
            request = MessageRequest(
                maxUserId=update.maxUserId,
                maxChatId=chatId,
                text=fitToLimit(transcript.text, REPLY_TEXT_MAX_LENGTH),
                source="voice",
                locale=update.locale,
                name=update.userName,
                asrLowConfidence=transcript.lowConfidence,
                asrDurationMs=transcript.durationMs,
            )
            return await self._replyFromAgent(chatId, lambda: self._d.agent.sendMessage(request))

    # Примечание: скачать сразу (ссылка живёт около суток) и распознать
    async def _transcribe(self, voice: VoiceRef) -> Transcript:
        audio = await self._d.downloader.download(voice)
        return await self._d.asr.transcribe(audio)

    # Примечание: /today, /tomorrow, /week → /v1/actions; /start и /help — справка; иначе подсказка
    async def _onCommand(self, update: IncomingUpdate, chatId: MaxChatId) -> HandleOutcome:
        name = (update.commandName or "").lower().split("@", 1)[0]
        if name in LOCAL_COMMANDS:
            return await self._sendText(chatId, self._d.texts.help())
        if name not in COMMAND_ACTIONS:
            return await self._sendText(chatId, self._d.texts.unknownCommand(name))
        action, args = COMMAND_ACTIONS[name]
        request = ActionRequest(
            maxUserId=update.maxUserId, maxChatId=chatId, action=action, args=args
        )
        return await self._askAgent(chatId, lambda: self._d.agent.sendAction(request))

    # Примечание: кнопка: ключ → действие из Redis → /v1/actions; спиннер MAX гасим сразу
    async def _onCallback(self, update: IncomingUpdate) -> HandleOutcome:
        callbackId = _callbackId(update)
        stored = await self._d.callbacks.load(update.callbackPayload or "")
        if stored is None or update.maxChatId is None:
            await self._d.gateway.answerCallback(callbackId, self._d.texts.buttonExpired())
            return HandleOutcome.BUTTON_EXPIRED
        await self._d.gateway.answerCallback(callbackId, None)
        request = ActionRequest(
            maxUserId=update.maxUserId,
            maxChatId=update.maxChatId,
            action=stored.action,
            args=stored.args,
            callbackId=callbackId,
        )
        return await self._askAgent(update.maxChatId, lambda: self._d.agent.sendAction(request))

    # Примечание: вызов агента под «печатает…»
    async def _askAgent(
        self, chatId: MaxChatId, call: Callable[[], Awaitable[Reply]]
    ) -> HandleOutcome:
        async with self._d.typing.during(chatId):
            return await self._replyFromAgent(chatId, call)

    # Примечание: Reply агента → сообщение MAX; сбой — текст с кодом цепочки, а не тишина
    async def _replyFromAgent(
        self, chatId: MaxChatId, call: Callable[[], Awaitable[Reply]]
    ) -> HandleOutcome:
        try:
            reply = await call()
            message = await self._d.mapper.toMessage(reply, chatId)
        except AppError as exc:
            _log.warning("agent_failed", error_code=exc.code)
            return await self._agentFailed(chatId)
        except Exception:  # noqa: BLE001 — пользователь получает ответ с кодом, а не тишину
            _log.exception("agent_failed", error_code=ErrorCode.INTERNAL)
            return await self._agentFailed(chatId)
        await self._d.gateway.sendMessage(message)
        return HandleOutcome.HANDLED

    # Примечание: «Не получилось связаться с ассистентом» + первые 6 символов trace_id
    async def _agentFailed(self, chatId: MaxChatId) -> HandleOutcome:
        await self._sendText(chatId, self._d.texts.agentFailed(shortTraceId()))
        return HandleOutcome.AGENT_FAILED

    # Примечание: «Слишком часто» — раз за окно; на кнопку — всегда, иначе MAX крутит индикатор
    async def _replyTooOften(self, update: IncomingUpdate, *, notify: bool) -> HandleOutcome:
        if update.kind is UpdateKind.CALLBACK:
            await self._d.gateway.answerCallback(_callbackId(update), self._d.texts.tooOften())
        elif notify and update.maxChatId is not None:
            await self._sendText(update.maxChatId, self._d.texts.tooOften())
        return HandleOutcome.RATE_LIMITED

    # Примечание: свой текст бота (без разметки) в чат
    async def _sendText(self, chatId: MaxChatId, text: str) -> HandleOutcome:
        await self._d.gateway.sendMessage(OutgoingMessage(chatId, fitToLimit(text)))
        return HandleOutcome.HANDLED


# Примечание: у callback-апдейта всегда есть callback_id (гарантирует маппер)
def _callbackId(update: IncomingUpdate) -> str:
    if update.callbackId is None:
        msg = f"callback без callback_id: {update.updateId}"
        raise ValueError(msg)
    return update.callbackId
