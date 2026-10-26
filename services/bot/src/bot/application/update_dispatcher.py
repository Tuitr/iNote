"""Очередь апдейтов: одного пользователя — строго по порядку, разных — параллельно.

Общий для long polling и webhook. На пользователя — своя очередь и одна задача-обработчик в
TaskRegistry; опустевшая очередь удаляется вместе с задачей. Одновременных обработок — не больше
maxConcurrent. При остановке реестр дожидается задач — принятые апдейты дорабатываются.
"""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Final

from bot.domain.updates import IncomingUpdate, MaxUserId
from common.lifecycle import TaskRegistry
from common.logging import getLogger
from common.trace import TraceId, currentTraceId, newTraceId, traceContext

type UpdateHandler = Callable[[IncomingUpdate], Awaitable[object]]

DEFAULT_MAX_CONCURRENT: Final = 16
# Очередь одного пользователя: лимит частоты (20 в минуту) всё равно отсечёт лишнее, а память
# под очередь без предела — риск при шквале апдейтов; сверх предела апдейт не принимается
MAX_QUEUE_PER_USER: Final = 50
TASK_PREFIX: Final = "updates"

_log = getLogger(__name__)


# Примечание: диспетчер апдейтов; handler — use case HandleUpdate, логики MAX здесь нет
class UpdateDispatcher:
    # Примечание: tasks — реестр приложения; maxConcurrent — общий предел обработок
    def __init__(
        self,
        handler: UpdateHandler,
        tasks: TaskRegistry,
        maxConcurrent: int = DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self._handler = handler
        self._tasks = tasks
        self._slots = asyncio.Semaphore(maxConcurrent)
        self._queues: dict[MaxUserId, deque[tuple[IncomingUpdate, TraceId]]] = {}
        self._idle = asyncio.Event()
        self._idle.set()

    # Примечание: число пользователей с непустой очередью (для тестов и логов)
    @property
    def activeUsers(self) -> int:
        return len(self._queues)

    # Примечание: принять апдейт и сразу вернуться; trace — текущий (webhook) или новый (polling).
    # Сервис останавливается — ShuttingDownError: апдейт не принят, MAX пришлёт его повторно
    def submit(self, update: IncomingUpdate) -> None:
        traceId = currentTraceId() or newTraceId()
        queue = self._queues.get(update.maxUserId)
        if queue is not None:
            if len(queue) >= MAX_QUEUE_PER_USER:
                _log.warning("update_dropped_queue_full", update_id=update.updateId)
                return
            queue.append((update, traceId))
            return
        queue = deque([(update, traceId)])
        # имя задачи попадает в лог при остановке — без max_user_id (правило 10), по первому апдейту
        _ = self._tasks.spawn(
            self._drain(update.maxUserId, queue), name=f"{TASK_PREFIX}:{update.updateId}"
        )
        self._queues[update.maxUserId] = queue
        self._idle.clear()

    # Примечание: дождаться, пока все принятые апдейты обработаны (polling фиксирует marker после)
    async def join(self) -> None:
        await self._idle.wait()

    # Примечание: очередь одного пользователя; пустая — удаляется (без await между проверкой и
    # удалением, поэтому новый апдейт не потеряется)
    async def _drain(self, userId: MaxUserId, queue: deque[tuple[IncomingUpdate, TraceId]]) -> None:
        try:
            while queue:
                update, traceId = queue.popleft()
                async with self._slots:
                    await self._processOne(update, traceId)
        finally:
            if self._queues.get(userId) is queue:
                del self._queues[userId]
            if not self._queues:
                self._idle.set()

    # Примечание: один апдейт в своём trace; ошибка апдейта не останавливает очередь
    async def _processOne(self, update: IncomingUpdate, traceId: TraceId) -> None:
        with traceContext(traceId):
            try:
                await self._handler(update)
            except Exception:  # noqa: BLE001
                _log.exception("update_failed", update_id=update.updateId, kind=update.kind.value)
