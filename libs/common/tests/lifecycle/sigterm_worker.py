"""Процесс для lifecycle-теста: фоновая задача + ожидание SIGTERM + штатная остановка."""

import asyncio
import json
import sys

from common.lifecycle import TaskRegistry, listenForShutdownSignal
from common.logging import configureLogging, getLogger


# Примечание: задача «обработка апдейта», которая должна доработать после SIGTERM
async def work(durationS: float) -> None:
    await asyncio.sleep(durationS)
    getLogger("worker").info("work_done")


# Примечание: аргументы — длительность задачи и таймаут остановки
async def main(durationS: float, shutdownTimeoutS: float) -> None:
    configureLogging("lifecycle-test", "INFO", "json")
    registry = TaskRegistry()
    _ = registry.spawn(work(durationS), name="update-1")
    # Обработчик сигнала ставится ДО «ready», иначе ранний SIGTERM убьёт процесс
    shutdownSignal = listenForShutdownSignal()
    getLogger("worker").info("ready")
    sig = await shutdownSignal
    getLogger("worker").info("signal_received", signal=sig.name)
    report = await registry.shutdown(shutdownTimeoutS)
    getLogger("worker").info(
        "stopped", completed=report.completed, cancelled=json.dumps(report.cancelled)
    )


if __name__ == "__main__":
    asyncio.run(main(float(sys.argv[1]), float(sys.argv[2])))
