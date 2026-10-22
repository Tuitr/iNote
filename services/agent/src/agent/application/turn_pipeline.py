"""Общий каркас хода для /v1/messages и /v1/actions: пользователь → лок → контекст → шаг → контекст.

Шаг (ход Brain, resume, действие без LLM) получает Session и возвращает StepOutcome; каркас держит
бюджет времени, лок, сохранение контекста и превращает любую беду в Reply с кодом и trace_id.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
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
    ToolExecutor,
    Turn,
    TurnInput,
    TurnResult,
    UserSettings,
)
from contracts.reply import Reply

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


# Примечание: что получает шаг: пользователь, контекст, «сейчас» в его поясе и инструменты хода
@dataclass(frozen=True)
class Session:
    user: UserContext
    context: DialogContext
    traceId: str
    now: datetime
    tools: ToolExecutor


# Примечание: итог шага: ответ и новый контекст (None — контекст не трогаем)
@dataclass(frozen=True)
class StepOutcome:
    reply: Reply
    context: DialogContext | None = None


type Step = Callable[[Session], Awaitable[StepOutcome]]


# Примечание: каркас хода; шаги — HandleMessage и HandleAction
class TurnPipeline:
    # Примечание: таймауты — из настроек; лок ждём внутри общего бюджета хода
    def __init__(
        self,
        deps: TurnDeps,
        *,
        turnTimeoutS: float = DEFAULT_TURN_TIMEOUT_S,
        lockWaitS: float = DEFAULT_LOCK_WAIT_S,
    ) -> None:
        self.deps = deps
        self._turnTimeoutS = turnTimeoutS
        self._lockWaitS = lockWaitS

    # Примечание: никогда не бросает наружу — любая беда превращается в Reply с кодом и trace_id
    async def execute(
        self, maxUserId: int, maxChatId: int, name: str | None, step: Step, *, kind: str
    ) -> Reply:
        traceId = currentTraceId() or newTraceId()
        started = time.perf_counter()
        renderer = self.deps.renderer
        try:
            async with asyncio.timeout(self._turnTimeoutS):
                reply = await self._withUser(maxUserId, maxChatId, name, step, traceId)
        except TimeoutError:
            _log.warning("turn_timeout", timeout_s=self._turnTimeoutS, kind=kind)
            reply = renderer.failure(ErrorCode.UPSTREAM_TIMEOUT, traceId)
        except AppError as exc:
            _log.warning("turn_failed", error_code=exc.code, kind=kind)
            reply = renderer.failure(exc.code, traceId)
        except Exception:  # noqa: BLE001 — пользователь получает ответ с кодом, а не обрыв
            _log.exception("turn_crashed", kind=kind)
            reply = renderer.failure(ErrorCode.INTERNAL, traceId)
        _log.info(
            f"{kind}_handled",
            state=reply.state.value,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return reply

    # Примечание: TurnInput без user_id (правило 1): «сейчас» уже в поясе пользователя
    def turnInput(
        self,
        session: Session,
        text: str,
        *,
        inputType: str = "text",
        asrLowConfidence: bool = False,
    ) -> TurnInput:
        user, context = session.user, session.context
        return TurnInput.model_validate(
            {
                "text": text,
                "inputType": inputType,
                "asrLowConfidence": asrLowConfidence,
                "now": session.now,
                "tz": user.tz,
                "history": list(context.history),
                "focus": list(context.focus),
                "pending": context.pending,
                "settings": UserSettings(
                    tz=user.tz,
                    defaultEventMin=user.defaultEventMin,
                    voiceReplies=user.voiceReplies,
                ),
            }
        )

    # Примечание: итог Brain (run или resume): правило llm_no_action → рендер → новый контекст
    def finish(
        self, session: Session, turn: TurnInput, raw: TurnResult, *, heard: str | None = None
    ) -> StepOutcome:
        result = enforceActionRule(raw)
        if result is not raw:
            _log.warning("llm_no_action", actions=len(raw.actions))
        reply = self.deps.renderer.render(result, session.user, session.traceId, heard=heard)
        _log.info(
            "turn_finished",
            outcome=result.outcome,
            error_code=result.errorCode,
            actions=[a.name for a in result.actions],
            llm_calls=result.usage.llmCalls,
        )
        return StepOutcome(reply, _nextContext(session.context, turn, result, reply))

    # Примечание: Reply по итогу действия без LLM: вызовы сделал сам агент, правило 3 соблюдено
    def render(self, session: Session, result: TurnResult) -> Reply:
        return self.deps.renderer.render(result, session.user, session.traceId)

    # Примечание: лок держим только на время шага; снимаем в finally — и при таймауте тоже
    async def _withUser(
        self, maxUserId: int, maxChatId: int, name: str | None, step: Step, traceId: str
    ) -> Reply:
        user = await self.deps.resolver.resolve(maxUserId, maxChatId, name)
        bindUserId(str(user.userId))
        token = await self.deps.lock.acquire(user.userId, self._lockWaitS)
        if token is None:
            _log.info("user_lock_busy", wait_s=self._lockWaitS)
            return self.deps.renderer.busy(traceId)
        try:
            context = await self.deps.contextStore.load(user.userId)
            session = Session(
                user=user,
                context=context,
                traceId=traceId,
                now=self.deps.clock.now().astimezone(ZoneInfo(user.tz)),
                tools=self.deps.tools.forTurn(
                    Caller(userId=user.userId, tz=user.tz, traceId=traceId)
                ),
            )
            outcome = await step(session)
            if outcome.context is not None:
                await self._saveContext(user, outcome.context)
            return outcome.reply
        finally:
            released = await self.deps.lock.release(user.userId, token)
            if not released:
                _log.warning("user_lock_lost")

    # Примечание: сбой записи контекста не отменяет ответ — теряется только память диалога
    async def _saveContext(self, user: UserContext, context: DialogContext) -> None:
        try:
            await self.deps.contextStore.save(user.userId, context)
        except Exception:  # noqa: BLE001
            _log.exception("context_save_failed")


# Примечание: история + реплики хода (до 10), фокус из ответов инструментов, pending и варианты
def _nextContext(
    context: DialogContext, turn: TurnInput, result: TurnResult, reply: Reply
) -> DialogContext:
    history = [
        *context.history,
        Turn(role="user", text=turn.text, at=turn.now),
        Turn(role="assistant", text=reply.text, at=turn.now),
    ][-HISTORY_LIMIT:]
    clarify = result.clarify
    return DialogContext(
        history=tuple(history),
        focus=nextFocus(context, result),
        pending=clarify.pending if clarify is not None else None,
        options=tuple(clarify.options) if clarify is not None else (),
    )


# Примечание: фокус — id из TurnResult.focus; данные — из ответов инструментов или прошлого фокуса
def nextFocus(context: DialogContext, result: TurnResult) -> tuple[ItemBrief, ...]:
    known = {brief.id: brief for brief in context.focus}
    known.update(briefsFromActions(result.actions))
    return tuple(known[itemId] for itemId in result.focus if itemId in known)
