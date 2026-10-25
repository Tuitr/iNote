"""POST /v1/messages: текст или распознанное голосовое → ход Brain внутри общего каркаса хода."""

from agent.application.ports import LlmQuota, Notice, UserInput
from agent.application.turn_pipeline import Session, StepOutcome, TurnPipeline
from common.logging import getLogger
from contracts.reply import MessageRequest, Reply

_log = getLogger(__name__)


# Примечание: ход по сообщению: TurnInput (с живым pending — Brain сам решает, ответ ли это) → run
class HandleMessage:
    # Примечание: каркас общий с HandleAction — лок, контекст и ошибки в одном месте
    # quota — дневной лимит ходов с LLM (только для AGENT_BRAIN=llm; ScriptedBrain бесплатный)
    def __init__(self, pipeline: TurnPipeline, quota: LlmQuota | None = None) -> None:
        self._pipeline = pipeline
        self._quota = quota

    # Примечание: никогда не бросает наружу — каркас превращает беду в Reply с кодом
    async def __call__(self, request: MessageRequest) -> Reply:
        # Примечание: шаг хода — Brain.run; «Распознал: …» — для голосового
        async def step(session: Session) -> StepOutcome:
            quota = self._quota
            if quota is not None and not await quota.allow(session.user.userId, session.now.date()):
                _log.info("llm_daily_limit")
                renderer = self._pipeline.deps.renderer
                return StepOutcome(renderer.notice(Notice.LLM_DAILY_LIMIT, session.traceId))
            voice = request.source == "voice"
            turn = self._pipeline.turnInput(
                session,
                request.text,
                inputType=request.source,
                asrLowConfidence=voice and request.asrLowConfidence,
            )
            raw = await self._pipeline.deps.brain.run(turn, session.tools)
            heard = request.text if voice else None
            return self._pipeline.finish(session, turn, raw, heard=heard)

        return await self._pipeline.execute(
            request.maxUserId,
            request.maxChatId,
            request.name,
            step,
            kind="message",
            said=UserInput(request.text, request.source),
        )
