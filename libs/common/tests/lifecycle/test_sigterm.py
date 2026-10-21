import json
import signal
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.lifecycle

WORKER = Path(__file__).with_name("sigterm_worker.py")


# Примечание: запускает процесс, ждёт «ready», шлёт SIGTERM и возвращает события логов
def runWithSigterm(durationS: float, shutdownTimeoutS: float) -> list[dict[str, object]]:
    proc = subprocess.Popen(  # noqa: S603 — запускаем свой скрипт текущим интерпретатором
        [sys.executable, str(WORKER), str(durationS), str(shutdownTimeoutS)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    lines: list[str] = []
    for line in proc.stdout:
        lines.append(line)
        if json.loads(line)["event"] == "ready":
            break
    proc.send_signal(signal.SIGTERM)
    rest, _ = proc.communicate(timeout=15)
    assert proc.returncode == 0
    return [json.loads(line) for line in [*lines, *rest.splitlines()] if line.strip()]


# Примечание: SIGTERM во время задачи — задача дорабатывает, потерь нет
def test_sigtermLetsTaskFinish() -> None:
    events = runWithSigterm(durationS=0.5, shutdownTimeoutS=5)
    names = [e["event"] for e in events]
    assert names.index("signal_received") < names.index("work_done") < names.index("stopped")
    stopped = events[-1]
    assert stopped["completed"] == 1
    assert stopped["cancelled"] == "[]"


# Примечание: задача дольше таймаута остановки — отменяется и логируется как потерянная
def test_sigtermCancelsAfterTimeout() -> None:
    events = runWithSigterm(durationS=30, shutdownTimeoutS=0.2)
    names = [e["event"] for e in events]
    assert "work_done" not in names
    assert "shutdown_tasks_cancelled" in names
    assert events[-1]["cancelled"] == json.dumps(["update-1"])
