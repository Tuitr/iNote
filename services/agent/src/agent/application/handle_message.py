"""POST /v1/messages: текст или распознанное голосовое → ход Brain внутри общего каркаса хода."""

from agent.application.ports import UserInput
from agent.application.turn_pipeline import Session, StepOutcome, TurnPipeline
from contracts.reply import MessageRequest, Reply


# Примечание: ход по сообщению: TurnInput (с живым pending — Brain сам решает, ответ ли это) → run
class HandleMessage:
    # Примечание: каркас общий с HandleAction — лок, контекст и ошибки в одном месте
    def __init__(self, pipeline: TurnPipeline) -> None:
        self._pipeline = pipeline

    # Примечание: никогда не бросает наружу — каркас превращает беду в Reply с кодом
    async def __call__(self, request: MessageRequest) -> Reply:
        # Примечание: шаг хода — Brain.run; «Распознал: …» — для голосового
        async def step(session: Session) -> StepOutcome:
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
