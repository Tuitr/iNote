import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Final

from bot.application.ports import MarkerStore, MaxGateway
from bot.domain.updates import IncomingUpdate, MaxUserId, UpdateBatch
from common.logging import getLogger
from common.trace import newTraceId, traceContext

type UpdateHandler = Callable[[IncomingUpdate], Awaitable[object]]

INITIAL_BACKOFF_S: Final = 1.0
MAX_BACKOFF_S: Final = 30.0

_log = getLogger(__name__)


# Примечание: цикл long polling: тянет апдейты, обрабатывает, marker фиксирует ПОСЛЕ обработки
class PollingLoop:
    # Примечание: handler — use case HandleUpdate; loop сам не знает бизнес-логики
    def __init__(
        self,
        *,
        gateway: MaxGateway,
        handler: UpdateHandler,
        markerStore: MarkerStore,
        pollTimeoutS: int,
        initialBackoffS: float = INITIAL_BACKOFF_S,
        maxBackoffS: float = MAX_BACKOFF_S,
    ) -> None:
        self._gateway = gateway
        self._handler = handler
        self._markerStore = markerStore
        self._pollTimeoutS = pollTimeoutS
        self._initialBackoffS = initialBackoffS
        self._maxBackoffS = maxBackoffS
        self._marker: int | None = None

    # Примечание: marker последней ПОЛНОСТЬЮ обработанной страницы
    @property
    def marker(self) -> int | None:
        return self._marker

    # Примечание: работает до stopping; начатую страницу дорабатывает, новую не берёт
    async def run(self, stopping: asyncio.Event) -> None:
        backoffS = self._initialBackoffS
        self._marker = await self._loadMarker()
        _log.info("polling_started", timeout_s=self._pollTimeoutS, marker=self._marker)
        while not stopping.is_set():
            try:
                batch = await self._fetchOrStop(stopping)
            except Exception:  # noqa: BLE001 — сбой MAX не должен убивать цикл
                _log.exception("polling_fetch_failed", backoff_s=backoffS)
                await self._sleepOrStop(backoffS, stopping)
                backoffS = min(backoffS * 2, self._maxBackoffS)
                continue
            if batch is None:
                break
            backoffS = self._initialBackoffS
            await self._processBatch(batch)
            await self._commitMarker(batch.marker)
        _log.info("polling_stopped", marker=self._marker)

    # Примечание: хранилище недоступно — начинаем с «ещё не полученных», дубли отсечёт дедуп
    async def _loadMarker(self) -> int | None:
        try:
            return await self._markerStore.load()
        except Exception:  # noqa: BLE001
            _log.exception("polling_marker_load_failed")
            return None

    # Примечание: фиксация после обработки; сбой записи не останавливает цикл
    async def _commitMarker(self, marker: int | None) -> None:
        self._marker = marker
        if marker is None:
            return
        try:
            await self._markerStore.save(marker)
        except Exception:  # noqa: BLE001
            _log.exception("polling_marker_save_failed", marker=marker)

    # Примечание: ждём апдейты или сигнал остановки — что раньше; отменённый fetch безопасен
    async def _fetchOrStop(self, stopping: asyncio.Event) -> UpdateBatch | None:
        fetch = asyncio.ensure_future(self._gateway.fetchUpdates(self._marker, self._pollTimeoutS))
        stop = asyncio.ensure_future(stopping.wait())
        await asyncio.wait({fetch, stop}, return_when=asyncio.FIRST_COMPLETED)
        if fetch.done():
            stop.cancel()
            return fetch.result()
        fetch.cancel()
        with suppress(asyncio.CancelledError):
            await fetch
        return None

    # Примечание: пауза после ошибки, прерываемая остановкой
    @staticmethod
    async def _sleepOrStop(delayS: float, stopping: asyncio.Event) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(stopping.wait(), timeout=delayS)

    # Примечание: апдейты одного пользователя — строго по порядку, разных — параллельно
    async def _processBatch(self, batch: UpdateBatch) -> None:
        byUser: dict[MaxUserId, list[IncomingUpdate]] = {}
        for update in batch.updates:
            byUser.setdefault(update.maxUserId, []).append(update)
        await asyncio.gather(*(self._processSequentially(items) for items in byUser.values()))

    # Примечание: очередь одного пользователя
    async def _processSequentially(self, updates: list[IncomingUpdate]) -> None:
        for update in updates:
            await self._processOne(update)

    # Примечание: бот — начало цепочки: новый trace_id на апдейт; ошибка апдейта не роняет цикл
    async def _processOne(self, update: IncomingUpdate) -> None:
        with traceContext(newTraceId()):
            try:
                await self._handler(update)
            except Exception:  # noqa: BLE001
                _log.exception("update_failed", update_id=update.updateId, kind=update.kind.value)
