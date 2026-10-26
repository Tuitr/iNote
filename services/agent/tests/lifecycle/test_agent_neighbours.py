"""Lifecycle агента с настоящими соседями (S4, шаг 2): MCP-заглушка C по HTTP и Redis.

1. SIGTERM посреди вызова MCP (заглушка «думает» 1,5 с): ответ отдан, лок снят, история
   дописана в «D», задачи не отменены.
2. Старт без Redis и без MCP: live 200, ready 503 со списком проверок; соседи поднялись — ready 200
   без перезапуска агента.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

import httpx
import pytest
import redis

from common.testing.pytest_plugin import redisOnPort
from services.agent.tests.conftest import REPO_ROOT, SERVICE_TOKEN

pytestmark = [pytest.mark.lifecycle, pytest.mark.integration]

STUB: Final = REPO_ROOT / "deploy" / "stub" / "mcp" / "server.py"
TOOL_DELAY_MS: Final = 1500
# как в deploy/docker/python.Dockerfile
GRACEFUL_S: Final = 15


# Примечание: свободный порт на loopback
def freePort() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


# Примечание: ждём, пока условие станет истинным; не дождались — падаем с пояснением
def waitUntil(predicate: Callable[[], bool], timeoutS: float, what: str) -> None:
    deadline = time.monotonic() + timeoutS
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(f"не дождались: {what}")


# Примечание: ответ /health/ready (None — не отвечает)
def ready(base: str) -> httpx.Response | None:
    try:
        return httpx.get(f"{base}/health/ready", timeout=2)
    except httpx.HTTPError:
        return None


# Примечание: /health/live отвечает 200
def isLive(base: str) -> bool:
    try:
        return httpx.get(f"{base}/health/live", timeout=0.5).status_code == 200
    except httpx.HTTPError:
        return False


# Примечание: записи JSON-лога (только строки JSON)
def records(logFile: Path) -> list[dict[str, object]]:
    lines = logFile.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.startswith("{")]


# Примечание: MCP-заглушка C в подпроцессе на заданном порту; delayMs — «медленный MCP»
@contextmanager
def mcpStub(port: int, logFile: Path, delayMs: int = 0) -> Iterator[None]:
    env = {
        "STUB_PORT": str(port),
        "SERVICE_TOKEN": SERVICE_TOKEN,
        "STUB_TOOL_DELAY_MS": str(delayMs),
        "PATH": os.environ.get("PATH", ""),
    }
    with logFile.open("ab") as out:
        proc = subprocess.Popen(  # noqa: S603 — своя заглушка из репо
            [sys.executable, str(STUB)], env=env, stdout=out, stderr=subprocess.STDOUT
        )
    try:
        waitUntil(lambda: isLive(f"http://127.0.0.1:{port}"), 15, "MCP-заглушка")
        yield
    finally:
        proc.terminate()
        proc.wait(timeout=10)


# Примечание: агент под uvicorn как в образе (ScriptedBrain, MCP и J8 — по HTTP)
@contextmanager
def agentProcess(
    port: int, redisUrl: str, mcpPort: int, logFile: Path
) -> Iterator[subprocess.Popen[bytes]]:
    env = {
        **os.environ,
        "SERVICE_TOKEN": SERVICE_TOKEN,
        "REDIS_URL": redisUrl,
        "LOG_FORMAT": "json",
        "AGENT_BRAIN": "scripted",
        "AGENT_MCP_URL": f"http://127.0.0.1:{mcpPort}/mcp",
        "AGENT_MCP_INTERNAL_URL": f"http://127.0.0.1:{mcpPort}",
    }
    command = [sys.executable, "-m", "uvicorn", "agent.main:createApp", "--factory"]
    options = ["--host", "127.0.0.1", "--port", str(port)]
    with logFile.open("w") as out:
        proc = subprocess.Popen(  # noqa: S603 — свой сервис текущим интерпретатором
            [*command, *options, "--timeout-graceful-shutdown", str(GRACEFUL_S)],
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=REPO_ROOT,
        )
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# Примечание: SIGTERM, пока MCP «думает» над list_items: ответ «Сегодня, …» отдан, лок снят,
# история хода дописана в заглушку до выхода, задачи не отменены, остановка штатная
def test_sigtermDuringMcpCall(redisContainerUrl: str, tmp_path: Path) -> None:
    port, mcpPort = freePort(), freePort()
    base, agentLog, stubLog = f"http://127.0.0.1:{port}", tmp_path / "agent.log", tmp_path / "s.log"
    maxUserId = 700_000_000 + int(time.time_ns() % 100_000_000)
    responses: list[httpx.Response] = []
    finished: list[float] = []
    with (
        mcpStub(mcpPort, stubLog, delayMs=TOOL_DELAY_MS),
        agentProcess(port, redisContainerUrl, mcpPort, agentLog) as proc,
    ):
        waitUntil(lambda: (r := ready(base)) is not None and r.status_code == 200, 20, "ready")

        # Примечание: бот ждёт ответ агента, пока MCP «думает»
        def send() -> None:
            body = {"max_user_id": maxUserId, "max_chat_id": 1, "text": "что у меня сегодня"}
            headers = {"Authorization": f"Bearer {SERVICE_TOKEN}"}
            responses.append(
                httpx.post(f"{base}/v1/messages", json=body, headers=headers, timeout=30)
            )
            finished.append(time.monotonic())

        thread = threading.Thread(target=send)
        thread.start()
        resolved = lambda: any(r.get("event") == "user_resolved" for r in records(stubLog))  # noqa: E731
        waitUntil(resolved, 10, "начало хода")
        time.sleep(0.5)  # ход уже в list_items: заглушка держит вызов 1,5 с
        sigtermAt = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        thread.join(timeout=30)
        proc.wait(timeout=30)
        userId = next(r["user_id"] for r in records(stubLog) if r.get("event") == "user_resolved")
    [response] = responses
    assert response.status_code == 200, agentLog.read_text()
    # ответ пришёл после SIGTERM — сигнал застал ход посреди вызова MCP
    assert finished[0] - sigtermAt > 0.3
    reply = response.json()
    assert reply["state"] == "answer", reply
    assert str(reply["text"]).startswith("Сегодня, ")
    client = redis.Redis.from_url(redisContainerUrl)
    assert client.exists(f"va:v1:agent:lock:{userId}") == 0
    client.close()
    events = [r.get("event") for r in records(agentLog)]
    assert events.index("agent_stopping") < events.index("agent_stopped")
    assert "shutdown_tasks_cancelled" not in events
    assert "dialog_turns_lost" not in events
    stored = [r for r in records(stubLog) if r.get("event") == "dialog_turns_stored"]
    assert [r["trace_id"] for r in stored] == [reply["trace_id"]]
    assert proc.returncode in {0, -signal.SIGTERM}


# Примечание: старт без Redis и MCP — живой, но не готов (503, обе проверки fail); соседи
# поднялись — готов без перезапуска; в readiness нет Brain (только redis и mcp)
def test_startsWithoutNeighbours(tmp_path: Path) -> None:
    port, redisPort, mcpPort = freePort(), freePort(), freePort()
    base = f"http://127.0.0.1:{port}"
    redisUrl = f"redis://127.0.0.1:{redisPort}/0"
    with agentProcess(port, redisUrl, mcpPort, tmp_path / "agent.log") as proc:
        waitUntil(lambda: isLive(base), 20, "live")
        first = ready(base)
        assert first is not None
        assert first.status_code == 503
        assert first.json()["checks"] == {"redis": "fail", "mcp": "fail"}
        with mcpStub(mcpPort, tmp_path / "s.log"), redisOnPort(redisPort):
            waitUntil(lambda: (r := ready(base)) is not None and r.status_code == 200, 30, "ready")
            final = ready(base)
            assert final is not None
            assert final.json()["checks"] == {"redis": "ok", "mcp": "ok"}
        assert proc.poll() is None  # тот же процесс, без перезапуска
