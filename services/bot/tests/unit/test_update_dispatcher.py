"""UpdateDispatcher: порядок по пользователю, параллельно между разными, общий предел, остановка."""

import asyncio
import dataclasses
import random

import pytest

from bot.application.update_dispatcher import MAX_QUEUE_PER_USER, UpdateDispatcher
from bot.domain.updates import IncomingUpdate
from common.lifecycle import ShuttingDownError, TaskRegistry
from common.trace import currentTraceId, traceContext
from services.bot.tests.fakes import loadIncoming

TRACE = "ab12f0c4d5e6f708192a3b4c5d6e7f80"


# Примечание: апдейт пользователя userId с номером n (свой update_id)
def update(userId: int, n: int) -> IncomingUpdate:
    return dataclasses.replace(loadIncoming("text"), maxUserId=userId, updateId=f"u{userId}-{n}")


# Примечание: обработчик-регистратор: случайная пауза, порядок, пик одновременных, trace
class Recorder:
    # Примечание: maxDelayS — разброс длительности обработки
    def __init__(self, maxDelayS: float = 0.01) -> None:
        self.maxDelayS = maxDelayS
        self.order: list[str] = []
        self.traces: dict[str, str | None] = {}
        self.running = 0
        self.peak = 0

    # Примечание: вызывается диспетчером для каждого апдейта
    async def __call__(self, item: IncomingUpdate) -> None:
        self.running += 1
        self.peak = max(self.peak, self.running)
        await asyncio.sleep(random.uniform(0, self.maxDelayS))  # noqa: S311 — не криптография
        self.order.append(item.updateId)
        self.traces[item.updateId] = currentTraceId()
        self.running -= 1


# Примечание: перемешанные апдейты двух пользователей — у каждого строго свой порядок,
# а между пользователями обработка идёт параллельно; опустевшие очереди удалены
async def test_orderPerUserInterleaved() -> None:
    recorder = Recorder()
    dispatcher = UpdateDispatcher(recorder, TaskRegistry())
    for n in range(10):
        dispatcher.submit(update(1, n))
        dispatcher.submit(update(2, n))
    await dispatcher.join()
    for userId in (1, 2):
        mine = [u for u in recorder.order if u.startswith(f"u{userId}-")]
        assert mine == [f"u{userId}-{n}" for n in range(10)]
    assert recorder.peak == 2
    assert dispatcher.activeUsers == 0


# Примечание: общий предел одновременных обработок — даже при 10 разных пользователях
async def test_concurrencyLimit() -> None:
    recorder = Recorder(maxDelayS=0.02)
    dispatcher = UpdateDispatcher(recorder, TaskRegistry(), maxConcurrent=3)
    for userId in range(10):
        dispatcher.submit(update(userId, 0))
    await dispatcher.join()
    assert len(recorder.order) == 10
    assert recorder.peak == 3


# Примечание: падение одного апдейта не останавливает очередь пользователя
async def test_failureDoesNotStopQueue() -> None:
    handled: list[str] = []

    # Примечание: первый апдейт падает
    async def handler(item: IncomingUpdate) -> None:
        if item.updateId.endswith("-0"):
            msg = "boom"
            raise RuntimeError(msg)
        handled.append(item.updateId)

    dispatcher = UpdateDispatcher(handler, TaskRegistry())
    for n in range(3):
        dispatcher.submit(update(1, n))
    await dispatcher.join()
    assert handled == ["u1-1", "u1-2"]


# Примечание: trace — текущий при приёме (webhook-запрос) или новый на апдейт (polling)
async def test_traceKeptOrCreated() -> None:
    recorder = Recorder(maxDelayS=0)
    dispatcher = UpdateDispatcher(recorder, TaskRegistry())
    with traceContext(TRACE):
        dispatcher.submit(update(1, 0))
    dispatcher.submit(update(2, 0))
    dispatcher.submit(update(3, 0))
    await dispatcher.join()
    traces = recorder.traces
    assert traces["u1-0"] == TRACE
    assert traces["u2-0"] is not None
    assert len({traces["u1-0"], traces["u2-0"], traces["u3-0"]}) == 3


# Примечание: остановка дожидается принятых апдейтов; после неё новое не принимается
async def test_shutdownDrainsAccepted() -> None:
    recorder = Recorder(maxDelayS=0.02)
    tasks = TaskRegistry()
    dispatcher = UpdateDispatcher(recorder, tasks)
    for n in range(5):
        dispatcher.submit(update(1, n))
    report = await tasks.shutdown(timeoutS=5)
    assert (len(recorder.order), report.clean) == (5, True)
    with pytest.raises(ShuttingDownError):
        dispatcher.submit(update(2, 0))
    assert dispatcher.activeUsers == 0


# Примечание: join без апдейтов не ждёт; новый апдейт после опустения — новая очередь
async def test_joinIdleAndReuse() -> None:
    recorder = Recorder(maxDelayS=0)
    dispatcher = UpdateDispatcher(recorder, TaskRegistry())
    await asyncio.wait_for(dispatcher.join(), timeout=0.1)
    dispatcher.submit(update(1, 0))
    await dispatcher.join()
    dispatcher.submit(update(1, 1))
    await dispatcher.join()
    assert recorder.order == ["u1-0", "u1-1"]


# Примечание: очередь одного пользователя ограничена — лишнее не принимается, остальные целы
async def test_queueLimitPerUser() -> None:
    gate = asyncio.Event()
    handled: list[str] = []

    # Примечание: первый апдейт держит очередь, пока тест не отпустит
    async def handler(item: IncomingUpdate) -> None:
        await gate.wait()
        handled.append(item.updateId)

    dispatcher = UpdateDispatcher(handler, TaskRegistry())
    for n in range(MAX_QUEUE_PER_USER + 10):
        dispatcher.submit(update(1, n))
    dispatcher.submit(update(2, 0))
    await asyncio.sleep(0)
    gate.set()
    await dispatcher.join()
    assert len([u for u in handled if u.startswith("u1-")]) == MAX_QUEUE_PER_USER
    assert "u2-0" in handled
