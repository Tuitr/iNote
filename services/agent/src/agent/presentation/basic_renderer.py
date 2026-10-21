"""ВРЕМЕННЫЙ рендерер S1, шаг 1: на шаге 5 его заменяет ReplyRenderer на шаблонах."""

from agent.domain.user import UserContext
from agent.presentation import templates
from common.trace import shortTraceId
from contracts.agent_side import TurnResult
from contracts.reply import Reply, ReplyState


# Примечание: (временно, до шага 5) Reply без шаблонов: say модели, «Готово» или ошибка с кодом
class BasicReplyRenderer:
    # Примечание: state совпадает с outcome; при ошибке — текст с первыми 6 символами trace_id
    def render(self, result: TurnResult, user: UserContext, traceId: str) -> Reply:
        state = ReplyState(result.outcome)
        if result.outcome == "error":
            return self.failure(result.errorCode or "internal", traceId)
        text = result.say or templates.DONE
        return Reply(text=text, state=state, traceId=traceId)

    # Примечание: лок пользователя занят
    def busy(self, traceId: str) -> Reply:
        return Reply(text=templates.BUSY, state=ReplyState.ERROR, traceId=traceId)

    # Примечание: ошибка по коду; пользователю — только короткий код цепочки
    def failure(self, errorCode: str, traceId: str) -> Reply:
        text = templates.ERROR.format(code=shortTraceId(traceId))
        return Reply(text=text, state=ReplyState.ERROR, traceId=traceId)
