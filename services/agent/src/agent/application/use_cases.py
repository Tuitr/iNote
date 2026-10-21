import time

from agent.application.ports import ReplySource
from common.logging import getLogger
from common.trace import currentTraceId, newTraceId
from contracts.reply import ActionRequest, MessageRequest, Reply

_log = getLogger(__name__)


# Примечание: trace_id в Reply — всегда текущей цепочки, а не тот, что был в источнике
def _withTrace(reply: Reply) -> Reply:
    return reply.model_copy(update={"traceId": currentTraceId() or newTraceId()})


# Примечание: POST /v1/messages: текст пользователя → Reply; в лог — без текста
class HandleMessage:
    # Примечание: источник ответа подставляет composition root
    def __init__(self, source: ReplySource) -> None:
        self._source = source

    # Примечание: в S0 только отдаёт ответ источника с текущим trace_id
    async def __call__(self, request: MessageRequest) -> Reply:
        started = time.perf_counter()
        reply = _withTrace(await self._source.forMessage(request))
        _log.info(
            "message_handled",
            source=request.source,
            state=reply.state.value,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return reply


# Примечание: POST /v1/actions: кнопка или команда → Reply без LLM
class HandleAction:
    # Примечание: источник ответа подставляет composition root
    def __init__(self, source: ReplySource) -> None:
        self._source = source

    # Примечание: имя действия в лог можно — это код кнопки, не текст пользователя
    async def __call__(self, request: ActionRequest) -> Reply:
        started = time.perf_counter()
        reply = _withTrace(await self._source.forAction(request))
        _log.info(
            "action_handled",
            action=request.action,
            state=reply.state.value,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        return reply
