import asyncio
import time
from dataclasses import dataclass
from typing import Final
from zoneinfo import ZoneInfo

from agent.application.ports import (
    Clock,
    DialogContext,
    DialogContextStore,
    ReplyRenderer,
    ToolExecutorFactory,
    UserLock,
    UserResolver,
)
from agent.application.turn_rules import briefsFromActions, enforceActionRule
from agent.domain.user import Caller, UserContext
from common.errors import AppError, ErrorCode
from common.logging import bindUserId, getLogger
from common.trace import currentTraceId, newTraceId
from contracts.agent_side import (
    HISTORY_LIMIT,
    Brain,
    ItemBrief,
    Turn,
    TurnInput,
    TurnResult,
    UserSettings,
)
from contracts.reply import MessageRequest, Reply

# Бюджет агента из стандартов: бот ждёт 45 с, агент укладывается в 40 с вместе с ожиданием лока
DEFAULT_TURN_TIMEOUT_S: Final = 40.0
DEFAULT_LOCK_WAIT_S: Final = 10.0

_log = getLogger(__name__)


# Примечание: зависимости хода — все порты, собираются в main.py
@dataclass(frozen=True)
class TurnDeps:
    resolver: UserResolver
    lock: UserLock
    contextStore: DialogContextStore
    clock: Clock
    brain: Brain
    tools: ToolExecutorFactory
    renderer: ReplyRenderer


# Примечание: POST /v1/messages: пользователь → лок → контекст → Brain → шаблон → контекст
class HandleMessage:
    # Примечание: таймауты — из настроек; лок ждём внутри общего бюджета хода
    def __init__(
        self,
        deps: TurnDeps,
        *,
        turnTimeoutS: float = DEFAULT_TURN_TIMEOUT_S,
        lockWaitS: float = DEFAULT_LOCK_WAIT_S,
    ) -> None:
        self._deps = deps
        self._turnTimeoutS = turnTimeoutS
        self._lockWaitS = lockWaitS

    # Примечание: никогда не бросает наружу — любая беда превращается в Reply с кодом и trace_id
    async def __call__(self, request: MessageRequest) -> Reply:
        traceId = currentTraceId() or newTraceId()
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._turnTimeoutS):
                reply = await self._withUser(request, traceId)
        except TimeoutError:
            _log.warning("turn_timeout", timeout_s=self._turnTimeoutS)
            reply = self._deps.renderer.failure(ErrorCode.UPSTREAM_TIMEOUT, traceId)
        except AppError as exc:
            _log.warning("turn_failed", error_code=exc.code)
            reply = self._deps.renderer.failure(exc.code, traceId)
        except Exception:  # noqa: BLE001 — пользователь получает ответ с кодом, а не обрыв
            _log.exception("turn_crashed")
            reply = self._deps.renderer.failure(ErrorCode.INTERNAL, traceId)
        _log.info(
            "message_handled",
            source=request.source,
            state=reply.state.value,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return reply

    # Примечание: лок держим только на время хода; снимаем в finally — и при таймауте тоже
    async def _withUser(self, request: MessageRequest, traceId: str) -> Reply:
        user = await self._deps.resolver.resolve(request.maxUserId, request.maxChatId, request.name)
        bindUserId(str(user.userId))
        token = await self._deps.lock.acquire(user.userId, self._lockWaitS)
        if token is None:
            _log.info("user_lock_busy", wait_s=self._lockWaitS)
            return self._deps.renderer.busy(traceId)
        try:
            return await self._turn(request, user, traceId)
        finally:
            released = await self._deps.lock.release(user.userId, token)
            if not released:
                _log.warning("user_lock_lost")

    # Примечание: сам ход: TurnInput без user_id, Brain через ToolExecutor, правило llm_no_action
    async def _turn(self, request: MessageRequest, user: UserContext, traceId: str) -> Reply:
        context = await self._deps.contextStore.load(user.userId)
        now = self._deps.clock.now().astimezone(ZoneInfo(user.tz))
        turn = TurnInput(
            text=request.text,
            inputType=request.source,
            asrLowConfidence=request.source == "voice" and request.asrLowConfidence,
            now=now,
            tz=user.tz,
            history=list(context.history),
            focus=list(context.focus),
            pending=context.pending,
            settings=UserSettings(
                tz=user.tz, defaultEventMin=user.defaultEventMin, voiceReplies=user.voiceReplies
            ),
        )
        tools = self._deps.tools.forTurn(Caller(userId=user.userId, tz=user.tz, traceId=traceId))
        raw = await self._deps.brain.run(turn, tools)
        result = enforceActionRule(raw)
        if result is not raw:
            _log.warning("llm_no_action", actions=len(raw.actions))
        heard = request.text if request.source == "voice" else None
        reply = self._deps.renderer.render(result, user, traceId, heard=heard)
        await self._saveContext(user, context, turn, result, reply)
        _log.info(
            "turn_finished",
            outcome=result.outcome,
            error_code=result.errorCode,
            actions=[a.name for a in result.actions],
            llm_calls=result.usage.llmCalls,
        )
        return reply

    # Примечание: сбой записи контекста не отменяет ответ — теряется только память диалога
    async def _saveContext(
        self,
        user: UserContext,
        context: DialogContext,
        turn: TurnInput,
        result: TurnResult,
        reply: Reply,
    ) -> None:
        history = [
            *context.history,
            Turn(role="user", text=turn.text, at=turn.now),
            Turn(role="assistant", text=reply.text, at=turn.now),
        ][-HISTORY_LIMIT:]
        updated = DialogContext(
            history=tuple(history),
            focus=tuple(_nextFocus(context, result)),
            pending=result.clarify.pending if result.clarify is not None else None,
        )
        try:
            await self._deps.contextStore.save(user.userId, updated)
        except Exception:  # noqa: BLE001
            _log.exception("context_save_failed")


# Примечание: фокус — id из TurnResult.focus; данные — из ответов инструментов или прошлого фокуса
def _nextFocus(context: DialogContext, result: TurnResult) -> list[ItemBrief]:
    known = {brief.id: brief for brief in context.focus}
    known.update(briefsFromActions(result.actions))
    return [known[itemId] for itemId in result.focus if itemId in known]
