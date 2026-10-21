import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Final

import httpx
import pytest
import redis

from services.agent.tests.conftest import REPO_ROOT, SERVICE_TOKEN

pytestmark = [pytest.mark.lifecycle, pytest.mark.integration]

WORKER: Final = Path(__file__).with_name("agent_worker.py")
MAX_USER: Final = 424242


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


# Примечание: SIGTERM во время хода: ответ доставлен, лок снят, остановка штатная
def test_sigtermDuringTurn(redisContainerUrl: str, tmp_path: Path) -> None:
    port = freePort()
    base = f"http://127.0.0.1:{port}"
    logFile = tmp_path / "agent.log"
    env = {
        **os.environ,
        "SERVICE_TOKEN": SERVICE_TOKEN,
        "REDIS_URL": redisContainerUrl,
        "LOG_FORMAT": "json",
    }
    with logFile.open("w") as out:
        proc = subprocess.Popen(  # noqa: S603 — свой сервис текущим интерпретатором
            [sys.executable, str(WORKER), str(port), "1.5"],
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=REPO_ROOT,
        )
    responses: list[httpx.Response] = []
    try:
        deadline = time.monotonic() + 15
        while not isReady(base):
            assert time.monotonic() < deadline, logFile.read_text()
            time.sleep(0.05)

        # Примечание: запрос бота к агенту в отдельном потоке — он висит, пока идёт ход
        def ask() -> None:
            responses.append(
                httpx.post(
                    f"{base}/v1/messages",
                    json={"max_user_id": MAX_USER, "max_chat_id": 1, "text": "привет"},
                    headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
                    timeout=10,
                )
            )

        thread = threading.Thread(target=ask)
        thread.start()
        client = redis.Redis.from_url(redisContainerUrl)
        lockKey = f"va:v1:agent:lock:{MAX_USER}"
        while client.exists(lockKey) == 0:
            assert time.monotonic() < deadline + 5, "ход не начался"
            time.sleep(0.02)
        proc.send_signal(signal.SIGTERM)
        thread.join(timeout=15)
        proc.wait(timeout=20)
        lockLeft = client.exists(lockKey)
        client.close()
    finally:
        if proc.poll() is None:
            proc.kill()
    [response] = responses
    assert response.status_code == 200, logFile.read_text()
    assert response.json()["text"] == "Дождался ответа"
    assert lockLeft == 0
    events = [json.loads(line).get("event") for line in logFile.read_text().splitlines()]
    assert events.index("message_handled") < events.index("agent_stopped")
    # uvicorn после штатной остановки заново поднимает SIGTERM (код -15) — это норма
    assert proc.returncode in {0, -signal.SIGTERM}
