"""POST /v1/actions: кнопки и команды без LLM — в том же каркасе хода (лок, контекст, caller).

Каталог (application/actions.py): pick_option → Brain.resume, cancel, undo → delete_item,
reschedule → update_item или вопрос «На когда перенести?», list_period → list_items, start и help;
complete, snooze, confirm_conflict — application/reminder_actions.py; settings, set_tz,
set_voice_replies — application/settings_actions.py.
Инструменты зовутся через ToolExecutor хода: caller и ключи идемпотентности ставит агент,
из args кнопки берутся только поля каталога. Незнакомое действие — «Не понял кнопку».
"""

import dataclasses
import time
from collections.abc import Callable, Mapping
from datetime import datetime

from agent.application.action_tools import (
    NOT_FOUND,
    callTool,
    errorCode,
    focusKind,
    itemIdArg,
    momentArg,
    toolResult,
)
from agent.application.actions import (
    CANCEL,
    CHOICE_ARG,
    COMPLETE,
    CONFIRM_CONFLICT,
    HELP,
    KIND_ARG,
    LIST_PERIOD,
    PERIOD_ARG,
    PICK_OPTION,
    RESCHEDULE,
    SET_TZ,
    SETTINGS,
    SNOOZE,
    START,
    STARTS_AT_ARG,
    UNDO,
)
from agent.application.item_time import ItemKind, isItemKind, moveArgs
from agent.application.ports import Notice, SettingsWriter, UserInput, Welcome
from agent.application.reminder_actions import ReminderActions
from agent.application.reschedule import rescheduleOptions
from agent.application.settings_actions import SettingsActions
from agent.application.turn_pipeline import (
    Session,
    Step,
    StepOutcome,
    TurnPipeline,
    nextFocus,
)
from agent.domain.periods import Period, isPeriod, periodRange
from common.errors import ErrorCode
from common.logging import getLogger
from common.trace import currentTraceId, newTraceId
from contracts.reply import ActionRequest, ArgValue, Reply

_log = getLogger(__name__)


# Примечание: действие → шаг каркаса; непонятное действие или args — без лока и инструментов
class HandleAction:
    # Примечание: всё нужное — в каркасе: renderer, Brain, инструменты хода
    def __init__(self, pipeline: TurnPipeline, settingsWriter: SettingsWriter) -> None:
        self._pipeline = pipeline
        self._reminders = ReminderActions(pipeline)
        self._settings = SettingsActions(pipeline, settingsWriter)

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
        # в историю — подпись кнопки (её прислал бот) или /действие; args не пишем (id и время)
        return await self._pipeline.execute(
            request.maxUserId,
            request.maxChatId,
            None,
            step,
            kind="action",
            said=UserInput(request.label or f"/{request.action}", "text"),
        )

    # Примечание: каталог действий; None — незнакомое действие или args не по каталогу
    def _step(self, action: str, args: Mapping[str, ArgValue]) -> Step | None:
        catalog: dict[str, Callable[[Mapping[str, ArgValue]], Step | None]] = {
            PICK_OPTION: lambda a: self._pickOption(a.get(CHOICE_ARG)),
            CANCEL: lambda _: self._cancel,
            UNDO: self._undoStep,
            RESCHEDULE: self._rescheduleStep,
            LIST_PERIOD: self._listStep,
            COMPLETE: self._reminders.completeStep,
            SNOOZE: self._reminders.snoozeStep,
            CONFIRM_CONFLICT: self._reminders.confirmConflictStep,
            SETTINGS: self._settings.settingsStep,
            SET_TZ: self._settings.setTzStep,
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
            return dataclasses.replace(outcome, said=UserInput(options[choice], "text"))

        return step

    # Примечание: «Отмена»: pending и варианты стираются, история не трогается
    async def _cancel(self, session: Session) -> StepOutcome:
        cleared = dataclasses.replace(session.context, pending=None, options=())
        reply = self._pipeline.deps.renderer.notice(Notice.CANCELLED, session.traceId)
        return StepOutcome(reply, cleared)

    # Примечание: undo без item_id или с чужим форматом id — «Не понял кнопку»
    def _undoStep(self, args: Mapping[str, ArgValue]) -> Step | None:
        itemId = itemIdArg(args)
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
            action = await callTool(session, "delete_item", {"id": itemId})
            if errorCode(action) == NOT_FOUND:
                renderer = self._pipeline.deps.renderer
                return StepOutcome(renderer.notice(Notice.ALREADY_UNDONE, session.traceId))
            result = toolResult(action, "done", started)
            focus = tuple(b for b in session.context.focus if not str(b.id).startswith(itemId))
            context = dataclasses.replace(session.context, focus=focus)
            return StepOutcome(self._pipeline.render(session, result), context)

        return step

    # Примечание: «Изменить время»: со starts_at — перенос, без — вопрос; starts_at не ISO — None
    def _rescheduleStep(self, args: Mapping[str, ArgValue]) -> Step | None:
        itemId = itemIdArg(args)
        if itemId is None:
            return None
        rawKind = args.get(KIND_ARG)
        if rawKind is not None and not isItemKind(rawKind):
            return None
        kind = rawKind if isItemKind(rawKind) else None
        if STARTS_AT_ARG not in args:
            return self._askWhen(itemId, kind)
        startsAt = momentArg(args[STARTS_AT_ARG])
        return None if startsAt is None else self._move(itemId, kind, startsAt)

    # Примечание: update_item с новым временем (у напоминания — remind_at); подтверждение — из
    # того, что вернул инструмент; вид записи — из кнопки, иначе из фокуса
    def _move(self, itemId: str, kind: ItemKind | None, startsAt: datetime) -> Step:
        # Примечание: шаг: перенос, фокус — на перенесённой записи
        async def step(session: Session) -> StepOutcome:
            started = time.perf_counter()
            known = kind or focusKind(session, itemId)
            args = moveArgs(itemId, known, startsAt)
            result = toolResult(await callTool(session, "update_item", args), "done", started)
            context = dataclasses.replace(
                session.context, focus=nextFocus(session.context, result) or session.context.focus
            )
            return StepOutcome(self._pipeline.render(session, result), context)

        return step

    # Примечание: «На когда перенести?»: варианты — кнопки reschedule с starts_at; текстом —
    # ответ разбирает Brain: фокус ставим на эту запись, прежний вопрос (pending) снимаем
    def _askWhen(self, itemId: str, kind: ItemKind | None) -> Step:
        # Примечание: шаг: время записи — из фокуса, если она там; иначе «завтра» — от «сейчас»
        async def step(session: Session) -> StepOutcome:
            context = session.context
            brief = next((b for b in context.focus if str(b.id).startswith(itemId)), None)
            options = rescheduleOptions(
                session.now, session.user.tz, brief.moment if brief else None
            )
            known = kind or (brief.kind if brief else None)
            reply = self._pipeline.deps.renderer.rescheduleQuestion(
                itemId, known, options, session.user, session.traceId
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
            # J3 у D: from и to — даты, to не включается
            span = periodRange(period, session.now, session.user.tz)
            args = {"from": span.start.date().isoformat(), "to": span.end.date().isoformat()}
            result = toolResult(await callTool(session, "list_items", args), "answer", started)
            context = dataclasses.replace(session.context, focus=nextFocus(session.context, result))
            return StepOutcome(self._pipeline.render(session, result), context)

        return step

    # Примечание: приветствие и справка; пользователь при этом уже создан через J8 (resolve)
    def _welcome(self, kind: Welcome) -> Step:
        # Примечание: шаг без инструментов и без изменения контекста
        async def step(session: Session) -> StepOutcome:
            return StepOutcome(self._pipeline.deps.renderer.welcome(kind, session.traceId))

        return step


# Примечание: номер варианта из args кнопки; не число или вне списка — None (старая кнопка)
def _choiceIndex(raw: object, count: int) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if 0 <= raw < count else None
