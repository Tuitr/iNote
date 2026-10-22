"""POST /v1/actions: кнопки и команды без LLM — в том же каркасе хода (лок, контекст, caller).

Каталог (application/actions.py): pick_option → Brain.resume, cancel, undo → delete_item,
reschedule → update_item или вопрос «На когда перенести?», list_period → list_items, start и help.
Инструменты зовутся через ToolExecutor хода: caller и ключи идемпотентности ставит агент,
из args кнопки берутся только поля каталога. Незнакомое действие — «Не понял кнопку».
"""

import dataclasses
import re
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Final, cast, get_args

from agent.application.actions import (
    CANCEL,
    CHOICE_ARG,
    HELP,
    ITEM_ID_ARG,
    LIST_PERIOD,
    PERIOD_ARG,
    PICK_OPTION,
    RESCHEDULE,
    START,
    STARTS_AT_ARG,
    UNDO,
)
from agent.application.ports import Notice, UserInput, Welcome
from agent.application.reschedule import rescheduleOptions
from agent.application.turn_pipeline import (
    Session,
    Step,
    StepOutcome,
    TurnPipeline,
    nextFocus,
)
from agent.application.turn_rules import briefsFromActions
from agent.domain.periods import Period, isPeriod, periodRange
from common.errors import ErrorCode
from common.logging import getLogger
from common.trace import currentTraceId, newTraceId
from contracts.agent_side import (
    ExecutedAction,
    ToolErrorCode,
    TurnOutcome,
    TurnResult,
    Usage,
)
from contracts.reply import ActionRequest, ArgValue, Reply

ACTION_MODEL: Final = "action"
NOT_FOUND: Final = "not_found"
# id записи из кнопки: uuid целиком или первые 8 символов (J3 принимает оба)
_ITEM_ID_RE: Final = re.compile(r"^[0-9a-fA-F-]{8,36}$")
_TOOL_ERRORS: Final = frozenset(get_args(ToolErrorCode.__value__))

_log = getLogger(__name__)


# Примечание: действие → шаг каркаса; непонятное действие или args — без лока и инструментов
class HandleAction:
    # Примечание: всё нужное — в каркасе: renderer, Brain, инструменты хода
    def __init__(self, pipeline: TurnPipeline) -> None:
        self._pipeline = pipeline

    # Примечание: имя действия в лог можно — это код кнопки, не текст пользователя
    async def __call__(self, request: ActionRequest) -> Reply:
        _log.info("action_received", action=request.action)
        step = self._step(request.action, request.args)
        if step is None:
            _log.warning(
                "action_rejected", action=request.action, error_code=ErrorCode.VALIDATION_ERROR
            )
            traceId = currentTraceId() or newTraceId()
            return self._pipeline.deps.renderer.notice(Notice.UNKNOWN_ACTION, traceId)
        # в историю — код действия (args не пишем: там id записей и время, не слова пользователя)
        return await self._pipeline.execute(
            request.maxUserId,
            request.maxChatId,
            None,
            step,
            kind="action",
            said=UserInput(request.action, "action"),
        )

    # Примечание: каталог действий; None — незнакомое действие или args не по каталогу
    def _step(self, action: str, args: Mapping[str, ArgValue]) -> Step | None:
        catalog: dict[str, Callable[[Mapping[str, ArgValue]], Step | None]] = {
            PICK_OPTION: lambda a: self._pickOption(a.get(CHOICE_ARG)),
            CANCEL: lambda _: self._cancel,
            UNDO: self._undoStep,
            RESCHEDULE: self._rescheduleStep,
            LIST_PERIOD: self._listStep,
            START: lambda _: self._welcome(Welcome.START),
            HELP: lambda _: self._welcome(Welcome.HELP),
        }
        build = catalog.get(action)
        return None if build is None else build(args)

    # Примечание: выбор варианта: живой pending → Brain.resume под тем же локом; нет — «устарел»
    def _pickOption(self, choiceArg: object) -> Step:
        # Примечание: шаг: номер варианта → подпись кнопки (её видел пользователь) → resume
        async def step(session: Session) -> StepOutcome:
            renderer = self._pipeline.deps.renderer
            choice = _choiceIndex(choiceArg, len(session.context.options))
            if session.context.pending is None or choice is None:
                _log.info("pending_expired", has_pending=session.context.pending is not None)
                return StepOutcome(renderer.notice(Notice.PENDING_EXPIRED, session.traceId))
            options = session.context.options
            turn = self._pipeline.turnInput(session, options[choice])
            raw = await self._pipeline.deps.brain.resume(turn, str(choice), session.tools)
            # в историю — подпись выбранной кнопки: это и есть ответ пользователя на вопрос
            outcome = self._pipeline.finish(session, turn, raw)
            return dataclasses.replace(outcome, said=UserInput(options[choice], "action"))

        return step

    # Примечание: «Отмена»: pending и варианты стираются, история не трогается
    async def _cancel(self, session: Session) -> StepOutcome:
        cleared = dataclasses.replace(session.context, pending=None, options=())
        reply = self._pipeline.deps.renderer.notice(Notice.CANCELLED, session.traceId)
        return StepOutcome(reply, cleared)

    # Примечание: undo без item_id или с чужим форматом id — «Не понял кнопку»
    def _undoStep(self, args: Mapping[str, ArgValue]) -> Step | None:
        itemId = _itemId(args)
        return None if itemId is None else self._undo(itemId)

    # Примечание: период только из каталога (today | tomorrow | week)
    def _listStep(self, args: Mapping[str, ArgValue]) -> Step | None:
        period = args.get(PERIOD_ARG)
        return self._list(period) if isPeriod(period) else None

    # Примечание: «Отменить» → delete_item; уже удалено (not_found) — «Уже отменено», не ошибка
    def _undo(self, itemId: str) -> Step:
        # Примечание: шаг: удаление и подтверждение из данных; запись уходит из фокуса
        async def step(session: Session) -> StepOutcome:
            started = time.perf_counter()
            action = await _callTool(session, "delete_item", {"id": itemId})
            if _errorCode(action) == NOT_FOUND:
                renderer = self._pipeline.deps.renderer
                return StepOutcome(renderer.notice(Notice.ALREADY_UNDONE, session.traceId))
            result = _result(action, "done", started)
            focus = tuple(b for b in session.context.focus if not str(b.id).startswith(itemId))
            context = dataclasses.replace(session.context, focus=focus)
            return StepOutcome(self._pipeline.render(session, result), context)

        return step

    # Примечание: «Изменить время»: со starts_at — перенос, без — вопрос; starts_at не ISO — None
    def _rescheduleStep(self, args: Mapping[str, ArgValue]) -> Step | None:
        itemId = _itemId(args)
        if itemId is None:
            return None
        if STARTS_AT_ARG not in args:
            return self._askWhen(itemId)
        startsAt = _moment(args[STARTS_AT_ARG])
        return None if startsAt is None else self._move(itemId, startsAt)

    # Примечание: update_item с новым временем; подтверждение — из того, что вернул инструмент
    def _move(self, itemId: str, startsAt: datetime) -> Step:
        # Примечание: шаг: перенос, фокус — на перенесённой записи
        async def step(session: Session) -> StepOutcome:
            started = time.perf_counter()
            args = {"id": itemId, "starts_at": startsAt.isoformat()}
            result = _result(await _callTool(session, "update_item", args), "done", started)
            context = dataclasses.replace(
                session.context, focus=nextFocus(session.context, result) or session.context.focus
            )
            return StepOutcome(self._pipeline.render(session, result), context)

        return step

    # Примечание: «На когда перенести?»: варианты — кнопки reschedule с starts_at; текстом —
    # ответ разбирает Brain: фокус ставим на эту запись, прежний вопрос (pending) снимаем
    def _askWhen(self, itemId: str) -> Step:
        # Примечание: шаг: время записи — из фокуса, если она там; иначе «завтра» — от «сейчас»
        async def step(session: Session) -> StepOutcome:
            context = session.context
            brief = next((b for b in context.focus if str(b.id).startswith(itemId)), None)
            options = rescheduleOptions(
                session.now, session.user.tz, brief.startsAt if brief else None
            )
            reply = self._pipeline.deps.renderer.rescheduleQuestion(
                itemId, options, session.user, session.traceId
            )
            focus = (brief,) if brief is not None else context.focus
            return StepOutcome(
                reply, dataclasses.replace(context, focus=focus, pending=None, options=())
            )

        return step

    # Примечание: /today, /tomorrow, /week и кнопки периода → list_items в поясе пользователя
    def _list(self, period: Period) -> Step:
        # Примечание: шаг: [from, to) от «сейчас» пользователя, неделя с понедельника
        async def step(session: Session) -> StepOutcome:
            started = time.perf_counter()
            span = periodRange(period, session.now, session.user.tz)
            args = {"from": span.start.isoformat(), "to": span.end.isoformat()}
            result = _result(await _callTool(session, "list_items", args), "answer", started)
            context = dataclasses.replace(session.context, focus=nextFocus(session.context, result))
            return StepOutcome(self._pipeline.render(session, result), context)

        return step

    # Примечание: приветствие и справка; пользователь при этом уже создан через J8 (resolve)
    def _welcome(self, kind: Welcome) -> Step:
        # Примечание: шаг без инструментов и без изменения контекста
        async def step(session: Session) -> StepOutcome:
            return StepOutcome(self._pipeline.deps.renderer.welcome(kind, session.traceId))

        return step


# Примечание: вызов инструмента хода; результат — как у Brain, для рендера из данных
async def _callTool(session: Session, name: str, args: Mapping[str, object]) -> ExecutedAction:
    result = await session.tools.call(name, args)
    _log.info("action_tool_called", tool=name, ok=result.ok, error_code=_code(result.error))
    return ExecutedAction(name=name, args=dict(args), result=result)


# Примечание: TurnResult из одного вызова: ошибка инструмента → outcome=error с его кодом
def _result(action: ExecutedAction, success: TurnOutcome, started: float) -> TurnResult:
    usage = Usage(
        model=ACTION_MODEL,
        llmCalls=0,
        tokensIn=0,
        tokensOut=0,
        latencyMs=round((time.perf_counter() - started) * 1000),
    )
    code = _errorCode(action)
    if code is not None:
        errorCode = cast("ToolErrorCode", code if code in _TOOL_ERRORS else "internal")
        return TurnResult(outcome="error", actions=[action], errorCode=errorCode, usage=usage)
    focus = list(briefsFromActions([action]))
    return TurnResult(outcome=success, actions=[action], focus=focus, usage=usage)


# Примечание: код ошибки вызова; None — успех
def _errorCode(action: ExecutedAction) -> str | None:
    return None if action.result.ok else (_code(action.result.error) or "internal")


# Примечание: код из ErrorInfo без падения на None
def _code(error: object) -> str | None:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else None


# Примечание: id записи из args кнопки; не строка или не похоже на uuid — None
def _itemId(args: Mapping[str, ArgValue]) -> str | None:
    raw = args.get(ITEM_ID_ARG)
    return raw.lower() if isinstance(raw, str) and _ITEM_ID_RE.match(raw) else None


# Примечание: время из args — ISO 8601 строго со смещением (правило 2)
def _moment(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.utcoffset() is not None else None


# Примечание: номер варианта из args кнопки; не число или вне списка — None (старая кнопка)
def _choiceIndex(raw: object, count: int) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if 0 <= raw < count else None
