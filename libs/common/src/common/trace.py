import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Final

import structlog

type TraceId = str

TRACE_ID_HEADER: Final = "X-Trace-Id"
TRACE_ID_BYTES: Final = 16
SHORT_TRACE_ID_LENGTH: Final = 6
_TRACE_ID_RE: Final = re.compile(r"^[0-9a-f]{32}$")

_traceIdVar: ContextVar[TraceId | None] = ContextVar("trace_id", default=None)


# Примечание: новый trace_id — 32 hex; создаёт только начало цепочки (бот, Scheduler, API)
def newTraceId() -> TraceId:
    return secrets.token_hex(TRACE_ID_BYTES)


# Примечание: чужой заголовок принимаем только в каноничном виде, иначе в логи попадёт мусор
def isValidTraceId(value: str) -> bool:
    return _TRACE_ID_RE.fullmatch(value) is not None


# Примечание: trace_id текущего запроса или задачи; None — вне цепочки
def currentTraceId() -> TraceId | None:
    return _traceIdVar.get()


# Примечание: первые 6 символов — то, что видит пользователь в сообщении об ошибке
def shortTraceId(traceId: TraceId | None = None) -> str:
    value = traceId if traceId is not None else currentTraceId()
    return (value or "")[:SHORT_TRACE_ID_LENGTH]


# Примечание: кладёт trace_id в contextvar и в контекст structlog, на выходе возвращает прежний
@contextmanager
def traceContext(traceId: TraceId) -> Iterator[TraceId]:
    token = _traceIdVar.set(traceId)
    try:
        with structlog.contextvars.bound_contextvars(trace_id=traceId):
            yield traceId
    finally:
        _traceIdVar.reset(token)
