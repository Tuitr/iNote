"""Кнопки напоминаний (J4) и конфликта встреч без LLM: complete, snooze, confirm_conflict.

- complete (item_id) → complete_item; уже выполнено — «Уже отмечено выполненным», не ошибка;
- snooze (item_id, minutes) → update_item с новым моментом напоминания
  (поле — item_time.snoozeField); «на N минут» — от «сейчас»;
- confirm_conflict (title, starts_at, ends_at?) → create_event с allow_conflict=true;
  (item_id, starts_at, ends_at?) → update_item с allow_conflict=true (перенос вопреки пересечению).
  Только пока жив вопрос о конфликте (pending); иначе — «Вопрос устарел».
Ответ — новым сообщением; старое сообщение напоминания не правим (MAX даёт править 24 ч).
"""

import dataclasses
import time
from collections.abc import Mapping
from typing import Final

from agent.application.action_tools import (
    callTool,
    focusKind,
    itemIdArg,
    momentArg,
    toolResult,
)
from agent.application.actions import (
    ENDS_AT_ARG,
    KIND_ARG,
    MINUTES_ARG,
    STARTS_AT_ARG,
    TITLE_ARG,
)
from agent.application.item_time import ItemKind, isItemKind, snoozeArgs
from agent.application.ports import DialogContext, Notice
from agent.application.snooze import MAX_SNOOZE_MINUTES, snoozeFor
from agent.application.turn_pipeline import Session, Step, StepOutcome, TurnPipeline, nextFocus
from contracts.agent_side import TurnResult
from contracts.reply import ArgValue

TITLE_MAX_LENGTH: Final = 200


# Примечание: шаги кнопок напоминаний и конфликта поверх общего каркаса хода
class ReminderActions:
    # Примечание: каркас общий с HandleAction: лок, контекст, caller и ключи идемпотентности
    def __init__(self, pipeline: TurnPipeline) -> None:
        self._pipeline = pipeline

    # Примечание: «Выполнено»: нужен item_id
    def completeStep(self, args: Mapping[str, ArgValue]) -> Step | None:
        itemId = itemIdArg(args)
        return None if itemId is None else self._complete(itemId)

    # Примечание: «Отложить»: item_id и minutes (1…1440)
    def snoozeStep(self, args: Mapping[str, ArgValue]) -> Step | None:
        itemId = itemIdArg(args)
        rawKind = args.get(KIND_ARG)
        if itemId is None or (rawKind is not None and not isItemKind(rawKind)):
            return None
        kind = rawKind if isItemKind(rawKind) else None
        minutes = args.get(MINUTES_ARG)
        if not _validMinutes(minutes):
            return None
        return self._snooze(itemId, kind, minutes=int(str(minutes)))

    # Примечание: «Всё равно создать»: название и начало обязательны, конец — если был
    def confirmConflictStep(self, args: Mapping[str, ArgValue]) -> Step | None:
        title = args.get(TITLE_ARG)
        itemId = itemIdArg(args)
        startsAt = momentArg(args.get(STARTS_AT_ARG))
        rawEnds = args.get(ENDS_AT_ARG)
        endsAt = momentArg(rawEnds) if rawEnds is not None else None
        if startsAt is None or (rawEnds is not None and endsAt is None):
            return None
        moment: dict[str, object] = {"starts_at": startsAt.isoformat(), "allow_conflict": True}
        if endsAt is not None:
            moment["ends_at"] = endsAt.isoformat()
        if itemId is not None:
            return self._confirmConflict("update_item", {"id": itemId, **moment})
        if not isinstance(title, str) or not 0 < len(title) <= TITLE_MAX_LENGTH:
            return None
        return self._confirmConflict("create_event", {"title": title, **moment})

    # Примечание: complete_item; повтор — «Уже отмечено выполненным»; удалённая — not_found
    def _complete(self, itemId: str) -> Step:
        # Примечание: шаг: отметить, подтверждение из данных, фокус — на этой записи
        async def step(session: Session) -> StepOutcome:
            # D на повторное «Выполнено» отвечает тем же «completed» (J3), поэтому повтор узнаём
            # по фокусу: после первого нажатия в нём эта запись со статусом done
            if any(
                str(brief.id).startswith(itemId) and brief.status == "done"
                for brief in session.context.focus
            ):
                notice = self._pipeline.deps.renderer.notice(
                    Notice.ALREADY_COMPLETED, session.traceId
                )
                return StepOutcome(notice)
            started = time.perf_counter()
            action = await callTool(session, "complete_item", {"id": itemId})
            result = toolResult(action, "done", started)
            return StepOutcome(
                self._pipeline.render(session, result), self._focused(session, result)
            )

        return step

    # Примечание: update_item с новым моментом напоминания; ответ «Напомню …»
    def _snooze(self, itemId: str, kind: ItemKind | None, *, minutes: int) -> Step:
        # Примечание: шаг: «сейчас» — часы хода в поясе пользователя
        async def step(session: Session) -> StepOutcome:
            started = time.perf_counter()
            moment = snoozeFor(session.now, session.user.tz, minutes)
            known = kind or focusKind(session, itemId)
            action = await callTool(session, "update_item", snoozeArgs(itemId, known, moment))
            result = toolResult(action, "done", started)
            renderer = self._pipeline.deps.renderer
            if result.outcome == "error":
                reply = self._pipeline.render(session, result)
            else:
                reply = renderer.snoozed(result, session.user, session.traceId)
            return StepOutcome(reply, self._focused(session, result))

        return step

    # Примечание: create_event или update_item вопреки пересечению — пока вопрос о конфликте жив
    def _confirmConflict(self, tool: str, args: dict[str, object]) -> Step:
        # Примечание: шаг: pending нет — «Вопрос устарел»; иначе вызов и снять вопрос
        async def step(session: Session) -> StepOutcome:
            renderer = self._pipeline.deps.renderer
            if session.context.pending is None:
                return StepOutcome(renderer.notice(Notice.PENDING_EXPIRED, session.traceId))
            started = time.perf_counter()
            result = toolResult(await callTool(session, tool, args), "done", started)
            context = dataclasses.replace(self._focused(session, result), pending=None, options=())
            return StepOutcome(self._pipeline.render(session, result), context)

        return step

    # Примечание: фокус — на записи из ответа инструмента (если есть), остальное как было
    @staticmethod
    def _focused(session: Session, result: TurnResult) -> DialogContext:
        focus = nextFocus(session.context, result) or session.context.focus
        return dataclasses.replace(session.context, focus=focus)


# Примечание: minutes — целое 1…1440 (не bool)
def _validMinutes(raw: object) -> bool:
    return isinstance(raw, int) and not isinstance(raw, bool) and 0 < raw <= MAX_SNOOZE_MINUTES
