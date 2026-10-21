import asyncio

import pytest
import structlog

from common.lifecycle import ShuttingDownError, TaskRegistry, managedTasks


# Примечание: задача, которая успевает доработать, учитывается как завершённая
async def test_shutdownWaitsForRunningTasks() -> None:
    registry = TaskRegistry()
    finished: list[str] = []

    # Примечание: имитация обработки, которая успевает завершиться
    async def work() -> None:
        await asyncio.sleep(0.05)
        finished.append("done")

    _ = registry.spawn(work(), name="work")
    report = await registry.shutdown(timeoutS=1.0)
    assert finished == ["done"]
    assert report.completed == 1
    assert report.clean


# Примечание: по таймауту задача отменяется, её имя попадает в отчёт и лог
async def test_shutdownCancelsAfterTimeout() -> None:
    registry = TaskRegistry()
    _ = registry.spawn(asyncio.sleep(10), name="slow")
    with structlog.testing.capture_logs() as logs:
        report = await registry.shutdown(timeoutS=0.05)
    assert report.cancelled == ("slow",)
    assert not report.clean
    assert any(e["event"] == "shutdown_tasks_cancelled" for e in logs)
    assert registry.activeCount == 0


# Примечание: после начала остановки новое не принимаем, stopping выставлен
async def test_spawnRejectedWhileClosing() -> None:
    registry = TaskRegistry()
    await registry.shutdown(timeoutS=0.1)
    assert registry.isClosing
    assert registry.stopping.is_set()
    with pytest.raises(ShuttingDownError):
        _ = registry.spawn(asyncio.sleep(0), name="late")


# Примечание: упавшая фоновая задача не теряется молча — пишется в лог
async def test_failedTaskIsLogged() -> None:
    registry = TaskRegistry()

    # Примечание: задача, падающая с исключением
    async def fail() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    with structlog.testing.capture_logs() as logs:
        task = registry.spawn(fail(), name="fails")
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)
    assert any(e["event"] == "background_task_failed" and e["task"] == "fails" for e in logs)
    assert registry.activeCount == 0


# Примечание: managedTasks дожидается задач при выходе из lifespan
async def test_managedTasksShutsDownOnExit() -> None:
    finished: list[bool] = []

    # Примечание: имитация обработки, которая успевает завершиться
    async def work() -> None:
        await asyncio.sleep(0.02)
        finished.append(True)

    async with managedTasks(timeoutS=1.0) as registry:
        _ = registry.spawn(work(), name="work")
    assert finished == [True]
