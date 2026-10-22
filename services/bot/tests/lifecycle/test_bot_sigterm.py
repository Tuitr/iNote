import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

import httpx
import pytest
import redis

from bot.infrastructure.max_mapper import deriveUpdateId
from services.bot.tests.fakes import loadMaxUpdate

pytestmark = [pytest.mark.lifecycle, pytest.mark.integration]

SEND_DELAY_MS: Final = 1500
REPO_ROOT: Final = Path(__file__).resolve().parents[4]
# Агент для readiness бота — stdlib-заглушка стенда: health 200, POST → 404 (бот ответит ошибкой)
AGENT_STUB: Final = REPO_ROOT / "deploy" / "stub" / "server.py"


# Примечание: свободный порт для uvicorn
def freePort() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


# Примечание: ждём, пока условие станет истинным
def waitUntil(predicate: object, timeoutS: float, what: str) -> None:
    assert callable(predicate)
    deadline = time.monotonic() + timeoutS
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(f"не дождались: {what}")


# Примечание: /health/ready отвечает 200
def isReady(base: str) -> bool:
    try:
        return httpx.get(f"{base}/health/ready", timeout=0.5).status_code == 200
    except httpx.HTTPError:
        return False


# Примечание: события JSON-логов процесса по порядку
def readEvents(logFile: Path) -> list[str]:
    events = []
    for line in logFile.read_text().splitlines():
        try:
            events.append(str(json.loads(line)["event"]))
        except (ValueError, KeyError):
            continue
    return events


# Примечание: SIGTERM посреди отправки ответа — ответ уходит, остановка штатная
def test_sigtermDuringProcessingDeliversReply(redisContainerUrl: str, tmp_path: Path) -> None:
    port, agentPort = freePort(), freePort()
    base = f"http://127.0.0.1:{port}"
    logFile = tmp_path / "bot.log"
    agent = subprocess.Popen(  # noqa: S603 — своя заглушка из репо
        [sys.executable, str(AGENT_STUB)],
        env={**os.environ, "STUB_NAME": "agent", "STUB_OWNER": "C", "STUB_PORT": str(agentPort)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    env = {
        **os.environ,
        "SERVICE_TOKEN": "t" * 32,
        "REDIS_URL": redisContainerUrl,
        "BOT_MAX_MODE": "fake",
        "BOT_POLL_TIMEOUT_S": "1",
        "BOT_SHUTDOWN_TIMEOUT_S": "10",
        "BOT_FAKE_SEND_DELAY_MS": str(SEND_DELAY_MS),
        "LOG_FORMAT": "json",
        "BOT_AGENT_URL": f"http://127.0.0.1:{agentPort}",
    }
    command = [sys.executable, "-m", "uvicorn", "bot.main:createApp", "--factory"]
    with logFile.open("w") as out:
        proc = subprocess.Popen(  # noqa: S603 — свой сервис текущим интерпретатором
            [*command, "--host", "127.0.0.1", "--port", str(port)],
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
        )
    try:
        waitUntil(lambda: isReady(base), 15, "ready")
        raw = loadMaxUpdate("text")
        httpx.post(f"{base}/dev/max/updates", json=raw, timeout=2).raise_for_status()
        # Ключ дедупа появился — обработка началась и сейчас «отправляет» ответ (1,5 с)
        message = raw["message"]
        assert isinstance(message, dict)
        dedupKey = f"va:v1:bot:upd:{deriveUpdateId('msg', message['body']['mid'])}"
        client = redis.Redis.from_url(redisContainerUrl)
        waitUntil(lambda: client.exists(dedupKey) == 1, 5, "начало обработки")
        client.close()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
        agent.terminate()
        agent.wait(timeout=5)
    events = readEvents(logFile)
    # uvicorn после штатной остановки заново поднимает SIGTERM (код -15) — это норма;
    # что остановка штатная, видно по логам: lifespan завершился, задачи не отменены
    assert proc.returncode in {0, -signal.SIGTERM}, logFile.read_text()
    assert "bot_stopped" in events
    assert events.index("bot_stopping") < events.index("max_message_sent")
    assert events.index("max_message_sent") < events.index("update_handled")
    assert events.index("update_handled") < events.index("polling_stopped")
    assert "shutdown_tasks_cancelled" not in events


# Примечание: webhook: два апдейта приняты (200), SIGTERM, пока первый «отправляется» —
# оба доработаны до остановки, задачи не отменены, цикла опроса нет
def test_sigtermWebhookFinishesAccepted(redisContainerUrl: str, tmp_path: Path) -> None:
    port, agentPort = freePort(), freePort()
    base = f"http://127.0.0.1:{port}"
    logFile = tmp_path / "bot.log"
    secret = "lifecycle-secret"
    agent = subprocess.Popen(  # noqa: S603 — своя заглушка из репо
        [sys.executable, str(AGENT_STUB)],
        env={**os.environ, "STUB_NAME": "agent", "STUB_OWNER": "C", "STUB_PORT": str(agentPort)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    env = {
        **os.environ,
        "SERVICE_TOKEN": "t" * 32,
        "REDIS_URL": redisContainerUrl,
        "BOT_MAX_MODE": "fake",
        "BOT_UPDATES_MODE": "webhook",
        "BOT_WEBHOOK_URL": f"{base}/webhook/max",
        "BOT_WEBHOOK_SECRET": secret,
        "BOT_SHUTDOWN_TIMEOUT_S": "10",
        "BOT_FAKE_SEND_DELAY_MS": str(SEND_DELAY_MS),
        "LOG_FORMAT": "json",
        "BOT_AGENT_URL": f"http://127.0.0.1:{agentPort}",
    }
    command = [sys.executable, "-m", "uvicorn", "bot.main:createApp", "--factory"]
    with logFile.open("w") as out:
        proc = subprocess.Popen(  # noqa: S603 — свой сервис текущим интерпретатором
            [*command, "--host", "127.0.0.1", "--port", str(port)],
            stdout=out,
            stderr=subprocess.STDOUT,
            env=env,
        )
    try:
        waitUntil(lambda: isReady(base), 15, "ready")
        first = loadMaxUpdate("text")
        second = json.loads(json.dumps(first))
        second["message"]["body"]["mid"] = "mid.lifecycle.second"
        for raw in (first, second):
            response = httpx.post(
                f"{base}/webhook/max",
                json=raw,
                headers={"X-Max-Bot-Api-Secret": secret},
                timeout=2,
            )
            assert response.status_code == 200
        # оба апдейта приняты, первый сейчас «отправляется» (1,5 с) — SIGTERM посреди обработки
        waitUntil(lambda: readEvents(logFile).count("webhook_accepted") == 2, 5, "приём")
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
    finally:
        if proc.poll() is None:
            proc.kill()
        agent.terminate()
        agent.wait(timeout=5)
    events = readEvents(logFile)
    assert proc.returncode in {0, -signal.SIGTERM}, logFile.read_text()
    assert events.count("update_handled") == 2
    assert events.index("bot_stopping") < events.index("max_message_sent")
    assert events.index("update_handled") < events.index("bot_stopped")
    assert "shutdown_tasks_cancelled" not in events
    assert "polling_started" not in events
