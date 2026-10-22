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


# Примечание: агент под uvicorn в подпроцессе; brainDelayS — ход, dialogDelayS — запись истории
def startWorker(
    redisUrl: str, tmp_path: Path, brainDelayS: float, dialogDelayS: float = 0.0
) -> tuple[subprocess.Popen[bytes], str]:
    port = freePort()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "SERVICE_TOKEN": SERVICE_TOKEN,
        "REDIS_URL": redisUrl,
        "LOG_FORMAT": "json",
        "DIALOG_FILE": str(tmp_path / "dialog.jsonl"),
        "DIALOG_DELAY_S": str(dialogDelayS),
    }
    with (tmp_path / "agent.log").open("w") as out:
        proc = subprocess.Popen(  # noqa: S603 — свой сервис текущим интерпретатором
            [sys.executable, str(WORKER), str(port), str(brainDelayS)],
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=REPO_ROOT,
        )
    deadline = time.monotonic() + 15
    while not isReady(base):
        assert time.monotonic() < deadline, (tmp_path / "agent.log").read_text()
        time.sleep(0.05)
    return proc, base


# Примечание: события JSON-лога агента по порядку
def events(logFile: Path) -> list[str]:
    lines = logFile.read_text().splitlines()
    return [json.loads(line).get("event") for line in lines if line.startswith("{")]


# Примечание: записи истории диалога, дошедшие до «D»
def dialogWritten(tmp_path: Path) -> list[dict[str, object]]:
    path = tmp_path / "dialog.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# Примечание: SIGTERM во время хода: ответ доставлен, лок снят, история дописана, остановка штатная
def test_sigtermDuringTurn(redisContainerUrl: str, tmp_path: Path) -> None:
    proc, base = startWorker(redisContainerUrl, tmp_path, brainDelayS=1.5)
    logFile = tmp_path / "agent.log"
    responses: list[httpx.Response] = []
    try:
        deadline = time.monotonic() + 15

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
    seen = events(logFile)
    assert (
        seen.index("message_handled")
        < seen.index("dialog_turns_saved")
        < seen.index("agent_stopped")
    )
    [written] = dialogWritten(tmp_path)
    assert written["trace_id"] == response.headers["X-Trace-Id"]
    # uvicorn после штатной остановки заново поднимает SIGTERM (код -15) — это норма
    assert proc.returncode in {0, -signal.SIGTERM}


# Примечание: SIGTERM сразу после ответа, пока запись истории ещё идёт: реестр её дожидается
# до закрытия HTTP-клиента — запись не теряется
def test_sigtermRightAfterTurn(redisContainerUrl: str, tmp_path: Path) -> None:
    proc, base = startWorker(redisContainerUrl, tmp_path, brainDelayS=0, dialogDelayS=1.0)
    try:
        response = httpx.post(
            f"{base}/v1/messages",
            json={"max_user_id": MAX_USER + 1, "max_chat_id": 1, "text": "привет"},
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
            timeout=10,
        )
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert response.status_code == 200
    [written] = dialogWritten(tmp_path)
    assert written["trace_id"] == response.headers["X-Trace-Id"]
    seen = events(tmp_path / "agent.log")
    assert "dialog_turns_lost" not in seen
    assert seen.index("dialog_turns_saved") < seen.index("agent_stopped")
