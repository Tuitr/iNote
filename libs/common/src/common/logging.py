import logging
import sys
from typing import Final

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

from common.config import LogFormat, LogLevel

# Поля, которые обязаны быть в каждой записи (docs/standards.md, «Логи»)
SERVICE_FIELD: Final = "service"
# uvicorn.access дублирует наш лог запроса из RequestContextMiddleware
_QUIET_LOGGERS: Final = ("uvicorn.access",)
_ROUTED_LOGGERS: Final = ("uvicorn", "uvicorn.error", "httpx", "httpcore")


# Примечание: процессор structlog, добавляющий имя сервиса в каждую запись
class _AddService:
    # Примечание: имя сервиса фиксируется один раз при настройке
    def __init__(self, serviceName: str) -> None:
        self._serviceName = serviceName

    # Примечание: не затирает service, если его явно передали в запись
    def __call__(self, logger: WrappedLogger, methodName: str, eventDict: EventDict) -> EventDict:
        eventDict.setdefault(SERVICE_FIELD, self._serviceName)
        return eventDict


# Примечание: единая настройка логов: structlog + stdlib (uvicorn, httpx) в один поток stdout
def configureLogging(
    serviceName: str, level: LogLevel = "INFO", fmt: LogFormat = "console"
) -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        _AddService(serviceName),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # False: тесты и повторная настройка в одном процессе видят новую конфигурацию
        cache_logger_on_first_use=False,
    )
    renderer: Processor
    if fmt == "json":
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
        finalizers: list[Processor] = [structlog.processors.format_exc_info, renderer]
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)
        finalizers = [renderer]
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *finalizers],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in _ROUTED_LOGGERS:
        routed = logging.getLogger(name)
        routed.handlers.clear()
        routed.propagate = True
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    # httpx на INFO пишет полный URL каждого запроса — шум и риск утечки параметров
    logging.getLogger("httpx").setLevel(max(logging.WARNING, root.level))


# Примечание: логгер с привязкой к contextvars (trace_id, user_id)
def getLogger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.stdlib.get_logger(name)
    return logger


# Примечание: привязать внутренний user_id к логам текущего контекста (не MAX id и не текст)
def bindUserId(userId: str) -> None:
    structlog.contextvars.bind_contextvars(user_id=userId)
