import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from services.agent.tests.conftest import REPO_ROOT, SERVICE_TOKEN

pytestmark = pytest.mark.lifecycle


# Примечание: свободный порт для uvicorn
def freePort() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


# Примечание: /health/ready отвечает 200
def isReady(base: str) -> bool:
    try:
        return httpx.get(f"{base}/health/ready", timeout=0.5).status_code == 200
    except httpx.HTTPError:
        return False


# Примечание: старт из корня репо (пути к примерам по умолчанию) → ready → SIGTERM → штатный выход
def test_startServeStop(tmp_path: Path) -> None:
    port = freePort()
    base = f"http://127.0.0.1:{port}"
    logFile = tmp_path / "agent.log"
    env = {**os.environ, "SERVICE_TOKEN": SERVICE_TOKEN, "LOG_FORMAT": "json"}
    with logFile.open("w") as out:
        proc = subprocess.Popen(  # noqa: S603 — свой сервис текущим интерпретатором
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agent.main:createApp",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=REPO_ROOT,
        )
    try:
        deadline = time.monotonic() + 15
        while not isReady(base):
            assert time.monotonic() < deadline, logFile.read_text()
            time.sleep(0.05)
        response = httpx.post(
            f"{base}/v1/messages",
            json={"max_user_id": 1, "max_chat_id": 2, "text": "привет"},
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            timeout=2,
        )
        assert response.json()["state"] == "done"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=15)
    finally:
        if proc.poll() is None:
            proc.kill()
    events = [json.loads(line).get("event") for line in logFile.read_text().splitlines()]
    # uvicorn после штатной остановки заново поднимает SIGTERM (код -15) — это норма
    assert proc.returncode in {0, -signal.SIGTERM}
    assert events.index("agent_started") < events.index("message_handled")
    assert events.index("message_handled") < events.index("agent_stopped")
