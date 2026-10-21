import asyncio
import dataclasses
from typing import override

from bot.domain.updates import IncomingUpdate, UpdateBatch
from bot.infrastructure.fake_max import FakeMaxGateway
from bot.infrastructure.memory_marker import InMemoryMarkerStore
from bot.presentation.polling import PollingLoop
from common.trace import currentTraceId, isValidTraceId
from services.bot.tests.fakes import loadIncoming, loadMaxUpdate


# Примечание: обработчик-регистратор: запоминает апдейты, trace_id и marker в момент обработки
class Recorder:
    # Примечание: delayS — имитация долгой обработки
    def __init__(self, loop: "PollingLoop | None" = None, delayS: float = 0.0) -> None:
        self.loop = loop
        self.delayS = delayS
        self.seen: list[tuple[str, str | None, int | None]] = []
        self.started = asyncio.Event()

    # Примечание: вызывается PollingLoop для каждого апдейта
    async def __call__(self, update: IncomingUpdate) -> None:
        self.started.set()
        await asyncio.sleep(self.delayS)
        marker = self.loop.marker if self.loop else None
        self.seen.append((update.updateId, currentTraceId(), marker))


# Примечание: дождаться условия с таймаутом
async def waitFor(predicate: object, timeoutS: float = 2.0) -> None:
    assert callable(predicate)
    async with asyncio.timeout(timeoutS):
        while not predicate():  # noqa: ASYNC110 — опрос состояния в тесте
            await asyncio.sleep(0.005)


# Примечание: marker фиксируется после обработки страницы; у каждого апдейта свой trace_id
async def test_markerCommittedAfterProcessing(gateway: FakeMaxGateway) -> None:
    recorder = Recorder()
    loop = PollingLoop(
        gateway=gateway, markerStore=InMemoryMarkerStore(), handler=recorder, pollTimeoutS=1
    )
    recorder.loop = loop
    stopping = asyncio.Event()
    task = asyncio.create_task(loop.run(stopping))
    await gateway.pushRaw(loadMaxUpdate("text"))
    await gateway.pushRaw(loadMaxUpdate("command"))
    await waitFor(lambda: len(recorder.seen) == 2)
    await waitFor(lambda: loop.marker == 2)
    stopping.set()
    await asyncio.wait_for(task, timeout=1)
    # Во время обработки marker ещё прежний (None) — фиксируется только после
    assert {m for _, _, m in recorder.seen} == {None}
    traceIds = [t for _, t, _ in recorder.seen]
    assert all(t is not None and isValidTraceId(t) for t in traceIds)
    assert len(set(traceIds)) == 2


# Примечание: апдейты одного пользователя — по порядку, даже если первый долгий
async def test_perUserOrder() -> None:
    first = loadIncoming("text")
    second = dataclasses.replace(loadIncoming("command"), maxUserId=first.maxUserId)
    other = dataclasses.replace(loadIncoming("bot_started"), maxUserId=42)
    order: list[str] = []

    # Примечание: первый апдейт пользователя обрабатывается дольше остальных
    async def handler(update: IncomingUpdate) -> None:
        await asyncio.sleep(0.05 if update is first else 0)
        order.append(update.updateId)

    # Примечание: шлюз, отдающий одну заранее заданную страницу
    class OnePage(FakeMaxGateway):
        # Примечание: первая выборка — страница, дальше пусто
        @override
        async def fetchUpdates(self, marker: int | None, timeoutS: int) -> UpdateBatch:
            if marker is None:
                return UpdateBatch(updates=(first, second, other), marker=3)
            await asyncio.sleep(timeoutS)
            return UpdateBatch(updates=(), marker=marker)

    loop = PollingLoop(
        gateway=OnePage(), markerStore=InMemoryMarkerStore(), handler=handler, pollTimeoutS=1
    )
    stopping = asyncio.Event()
    task = asyncio.create_task(loop.run(stopping))
    await waitFor(lambda: len(order) == 3)
    stopping.set()
    await task
    assert order.index(first.updateId) < order.index(second.updateId)
    assert order[0] == other.updateId


# Примечание: падение обработки одного апдейта не останавливает цикл
async def test_failingUpdateDoesNotStopLoop(gateway: FakeMaxGateway) -> None:
    handled: list[str] = []

    # Примечание: падает на команде, остальное обрабатывает
    async def handler(update: IncomingUpdate) -> None:
        if update.kind.value == "command":
            msg = "boom"
            raise RuntimeError(msg)
        handled.append(update.kind.value)

    loop = PollingLoop(
        gateway=gateway, markerStore=InMemoryMarkerStore(), handler=handler, pollTimeoutS=1
    )
    stopping = asyncio.Event()
    task = asyncio.create_task(loop.run(stopping))
    for name in ("command", "text"):
        await gateway.pushRaw(loadMaxUpdate(name))
    await waitFor(lambda: handled == ["text"] and loop.marker == 2)
    stopping.set()
    await task


# Примечание: остановка во время ожидания long polling — сразу, не через 30 секунд
async def test_stopsWhileWaiting(gateway: FakeMaxGateway) -> None:
    loop = PollingLoop(
        gateway=gateway, markerStore=InMemoryMarkerStore(), handler=Recorder(), pollTimeoutS=30
    )
    stopping = asyncio.Event()
    task = asyncio.create_task(loop.run(stopping))
    await asyncio.sleep(0.02)
    stopping.set()
    await asyncio.wait_for(task, timeout=0.5)


# Примечание: остановка во время обработки — апдейт дорабатывается, новые не берутся
async def test_stopDuringProcessingFinishesUpdate(gateway: FakeMaxGateway) -> None:
    recorder = Recorder(delayS=0.1)
    loop = PollingLoop(
        gateway=gateway, markerStore=InMemoryMarkerStore(), handler=recorder, pollTimeoutS=1
    )
    stopping = asyncio.Event()
    task = asyncio.create_task(loop.run(stopping))
    await gateway.pushRaw(loadMaxUpdate("text"))
    await asyncio.wait_for(recorder.started.wait(), timeout=1)
    stopping.set()
    await gateway.pushRaw(loadMaxUpdate("command"))
    await asyncio.wait_for(task, timeout=1)
    assert len(recorder.seen) == 1
    assert loop.marker == 1


# Примечание: сбой MAX при выборке — пауза и повтор, цикл живёт
async def test_fetchErrorRetried() -> None:
    calls: list[int | None] = []

    # Примечание: первая выборка падает, вторая отдаёт апдейт
    class Flaky(FakeMaxGateway):
        # Примечание: падает один раз
        @override
        async def fetchUpdates(self, marker: int | None, timeoutS: int) -> UpdateBatch:
            calls.append(marker)
            if len(calls) == 1:
                msg = "MAX 500"
                raise ConnectionError(msg)
            return await super().fetchUpdates(marker, timeoutS)

    gateway = Flaky()
    recorder = Recorder()
    loop = PollingLoop(
        gateway=gateway,
        markerStore=InMemoryMarkerStore(),
        handler=recorder,
        pollTimeoutS=1,
        initialBackoffS=0.01,
    )
    stopping = asyncio.Event()
    task = asyncio.create_task(loop.run(stopping))
    await gateway.pushRaw(loadMaxUpdate("text"))
    await waitFor(lambda: len(recorder.seen) == 1)
    stopping.set()
    await task
    assert len(calls) >= 2


# Примечание: marker берётся из хранилища на старте и сохраняется туда после страницы
async def test_markerLoadedAndSaved(gateway: FakeMaxGateway) -> None:
    store = InMemoryMarkerStore()
    await gateway.pushRaw(loadMaxUpdate("text"))
    await gateway.pushRaw(loadMaxUpdate("command"))
    await store.save(1)
    recorder = Recorder()
    loop = PollingLoop(gateway=gateway, markerStore=store, handler=recorder, pollTimeoutS=1)
    stopping = asyncio.Event()
    task = asyncio.create_task(loop.run(stopping))
    await waitFor(lambda: len(recorder.seen) == 1)
    await waitFor(lambda: loop.marker == 2)
    stopping.set()
    await task
    # С marker=1 первый апдейт не перечитан — обработан только второй
    assert recorder.seen[0][0] == loadIncoming("command").updateId
    assert await store.load() == 2


# Примечание: хранилище marker сломано — цикл всё равно работает
async def test_brokenMarkerStoreDoesNotStopLoop(gateway: FakeMaxGateway) -> None:
    # Примечание: хранилище, которое всегда падает
    class Broken(InMemoryMarkerStore):
        # Примечание: чтение падает
        @override
        async def load(self) -> int | None:
            msg = "redis down"
            raise ConnectionError(msg)

        # Примечание: запись падает
        @override
        async def save(self, marker: int) -> None:
            msg = "redis down"
            raise ConnectionError(msg)

    recorder = Recorder()
    loop = PollingLoop(gateway=gateway, markerStore=Broken(), handler=recorder, pollTimeoutS=1)
    stopping = asyncio.Event()
    task = asyncio.create_task(loop.run(stopping))
    await gateway.pushRaw(loadMaxUpdate("text"))
    await waitFor(lambda: len(recorder.seen) == 1 and loop.marker == 1)
    stopping.set()
    await task


# Примечание: голосовое в формате MAX от user с уникальным mid
def voiceRaw(mid: str, user: int) -> dict[str, object]:
    raw = loadMaxUpdate("voice")
    message = raw["message"]
    assert isinstance(message, dict)
    message["body"] = {**message["body"], "mid": mid}
    message["sender"] = {**message["sender"], "user_id": user}
    return raw


# Примечание: два голосовых подряд от одного пользователя — по очереди, у разных — параллельно
async def test_voicesOfOneUserInOrder() -> None:
    events: list[str] = []
    allDone = asyncio.Event()

    # Примечание: «долгое» распознавание: начало и конец обработки каждого апдейта
    async def slowHandler(update: IncomingUpdate) -> None:
        events.append(f"start:{update.maxUserId}:{update.updateId}")
        await asyncio.sleep(0.05)
        events.append(f"end:{update.maxUserId}:{update.updateId}")
        if sum(e.startswith("end:") for e in events) == 3:
            allDone.set()

    gateway = FakeMaxGateway()
    for mid, user in (("m1", 1), ("m2", 1), ("m3", 2)):
        await gateway.pushRaw(voiceRaw(mid, user))
    loop = PollingLoop(
        gateway=gateway, handler=slowHandler, markerStore=InMemoryMarkerStore(), pollTimeoutS=0
    )
    stopping = asyncio.Event()
    running = asyncio.create_task(loop.run(stopping))
    async with asyncio.timeout(2):
        await allDone.wait()
    stopping.set()
    await running
    firstUser = [e for e in events if ":1:" in e]
    assert [e.split(":")[0] for e in firstUser] == ["start", "end", "start", "end"]
    starts = [i for i, e in enumerate(events) if e.startswith("start:")]
    assert events[starts[1]].startswith("start:2:")  # второй пользователь не ждёт первого
