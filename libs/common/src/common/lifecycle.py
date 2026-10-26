import asyncio
import signal
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import Final

from common.logging import getLogger

DEFAULT_SHUTDOWN_TIMEOUT_S: Final = 10.0
_SHUTDOWN_SIGNALS: Final = (signal.SIGTERM, signal.SIGINT)

_log = getLogger(__name__)


# Примечание: попытка запустить задачу, когда сервис уже останавливается
class ShuttingDownError(RuntimeError):
    pass


# Примечание: итог остановки — что доработало и что пришлось отменить
@dataclass(frozen=True)
class ShutdownReport:
    completed: int
    cancelled: tuple[str, ...] = field(default_factory=tuple)

    # Примечание: True, если ни одна задача не потеряна
    @property
    def clean(self) -> bool:
        return not self.cancelled


# Примечание: реестр фоновых задач; задача вне реестра теряется при SIGTERM (правило 9)
class TaskRegistry:
    # Примечание: stopping — сигнал циклам (long polling) перестать брать новую работу
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[object]] = set()
        self._closing = False
        self.stopping = asyncio.Event()

    # Примечание: True после начала остановки — новое не принимаем
    @property
    def isClosing(self) -> bool:
        return self._closing

    # Примечание: число живых задач (для health и тестов)
    @property
    def activeCount(self) -> int:
        return len(self._tasks)

    # Примечание: запускает и регистрирует задачу; после начала остановки — ShuttingDownError
    def spawn[T](self, coro: Coroutine[object, object, T], name: str) -> asyncio.Task[T]:
        if self._closing:
            coro.close()
            msg = f"сервис останавливается, задача {name} не принята"
            raise ShuttingDownError(msg)
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._onDone)
        return task

    # Примечание: снимает задачу с учёта и логирует её падение — иначе ошибка тихо пропадёт
    def _onDone(self, task: asyncio.Task[object]) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _log.error("background_task_failed", task=task.get_name(), exc_info=exc)

    # Примечание: перестаёт принимать новое, ждёт текущие до таймаута, остаток отменяет
    async def shutdown(self, timeoutS: float = DEFAULT_SHUTDOWN_TIMEOUT_S) -> ShutdownReport:
        self._closing = True
        self.stopping.set()
        pending = set(self._tasks)
        if not pending:
            return ShutdownReport(completed=0)
        _log.info("shutdown_waiting", tasks=len(pending), timeout_s=timeoutS)
        done, notDone = await asyncio.wait(pending, timeout=timeoutS)
        lost = tuple(sorted(task.get_name() for task in notDone))
        for task in notDone:
            task.cancel()
        if notDone:
            await asyncio.gather(*notDone, return_exceptions=True)
            _log.warning("shutdown_tasks_cancelled", tasks=list(lost))
        return ShutdownReport(completed=len(done), cancelled=lost)


# Примечание: хелпер для FastAPI lifespan — реестр живёт ровно столько, сколько приложение
@asynccontextmanager
async def managedTasks(
    timeoutS: float = DEFAULT_SHUTDOWN_TIMEOUT_S,
) -> AsyncIterator[TaskRegistry]:
    registry = TaskRegistry()
    try:
        yield registry
    finally:
        await registry.shutdown(timeoutS)


# Примечание: ставит обработчики SIGTERM/SIGINT сразу — до объявления «готов»; uvicorn ловит их сам
def listenForShutdownSignal() -> asyncio.Future[signal.Signals]:
    loop = asyncio.get_running_loop()
    received: asyncio.Future[signal.Signals] = loop.create_future()
    for sig in _SHUTDOWN_SIGNALS:
        loop.add_signal_handler(sig, _resolveOnce, received, sig)
    received.add_done_callback(lambda _: _removeSignalHandlers(loop))
    return received


# Примечание: возвращает сигналам поведение по умолчанию после первого срабатывания
def _removeSignalHandlers(loop: asyncio.AbstractEventLoop) -> None:
    for sig in _SHUTDOWN_SIGNALS:
        with suppress(ValueError, RuntimeError):
            loop.remove_signal_handler(sig)


# Примечание: второй сигнал подряд не должен падать на уже завершённом future
def _resolveOnce(future: asyncio.Future[signal.Signals], sig: signal.Signals) -> None:
    if not future.done():
        future.set_result(sig)
