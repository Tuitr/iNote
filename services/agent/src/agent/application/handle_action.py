"""POST /v1/actions: кнопки и команды без LLM — в том же каркасе хода (лок, контекст, caller).

S2, шаг 3: выбор варианта уточнения (pick_option → Brain.resume) и «Отмена» (cancel).
Остальные действия каталога — шаг 4; до него — фиксированный ответ из contracts/examples.
"""

import dataclasses
from typing import Final

from agent.application.actions import CANCEL, PICK_OPTION
from agent.application.ports import Notice, ReplySource
from agent.application.turn_pipeline import Session, Step, StepOutcome, TurnPipeline
from common.logging import getLogger
from common.trace import currentTraceId, newTraceId
from contracts.reply import ActionRequest, Reply

CHOICE_ARG: Final = "choice"

_log = getLogger(__name__)


# Примечание: действие → шаг каркаса; незнакомое пока уходит в заглушку (шаг 4 S2)
class HandleAction:
    # Примечание: fallback — фиксированный Reply для действий, которые ещё не сделаны
    def __init__(self, pipeline: TurnPipeline, fallback: ReplySource) -> None:
        self._pipeline = pipeline
        self._fallback = fallback

    # Примечание: имя действия в лог можно — это код кнопки, не текст пользователя
    async def __call__(self, request: ActionRequest) -> Reply:
        _log.info("action_received", action=request.action)
        steps: dict[str, Step] = {PICK_OPTION: self._pickOption(request), CANCEL: self._cancel}
        step = steps.get(request.action)
        if step is None:
            reply = await self._fallback.forAction(request)
            return reply.model_copy(update={"traceId": currentTraceId() or newTraceId()})
        return await self._pipeline.execute(
            request.maxUserId, request.maxChatId, None, step, kind="action"
        )

    # Примечание: выбор варианта: живой pending → Brain.resume под тем же локом; нет — «устарел»
    def _pickOption(self, request: ActionRequest) -> Step:
        # Примечание: шаг: номер варианта → подпись кнопки (её видел пользователь) → resume
        async def step(session: Session) -> StepOutcome:
            renderer = self._pipeline.deps.renderer
            choice = _choiceIndex(request.args.get(CHOICE_ARG), len(session.context.options))
            if session.context.pending is None or choice is None:
                _log.info("pending_expired", has_pending=session.context.pending is not None)
                return StepOutcome(renderer.notice(Notice.PENDING_EXPIRED, session.traceId))
            options = session.context.options
            turn = self._pipeline.turnInput(session, options[choice])
            raw = await self._pipeline.deps.brain.resume(turn, str(choice), session.tools)
            return self._pipeline.finish(session, turn, raw)

        return step

    # Примечание: «Отмена»: pending и варианты стираются, история не трогается
    async def _cancel(self, session: Session) -> StepOutcome:
        cleared = dataclasses.replace(session.context, pending=None, options=())
        reply = self._pipeline.deps.renderer.notice(Notice.CANCELLED, session.traceId)
        return StepOutcome(reply, cleared)


# Примечание: номер варианта из args кнопки; не число или вне списка — None (старая кнопка)
def _choiceIndex(raw: object, count: int) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    return raw if 0 <= raw < count else None
