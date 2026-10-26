import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Final

import httpx
import pytest
import redis

from bot.infrastructure.max_mapper import deriveUpdateId
from services.bot.tests.fakes import EXAMPLES, loadMaxUpdate

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


# Примечание: апдейт-текст с уникальным mid: Redis контейнера общий на сессию тестов, и тот же
# mid из другого теста дедуп отсёк бы как повтор
def uniqueText() -> dict[str, object]:
    raw = loadMaxUpdate("text")
    message = raw["message"]
    assert isinstance(message, dict)
    message["body"]["mid"] = f"mid.lifecycle.{uuid.uuid4().hex}"
    return raw


# Примечание: записи JSON-логов процесса с событием name, по порядку
def readRecords(logFile: Path, name: str) -> list[dict[str, object]]:
    found = []
    for line in logFile.read_text().splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and record.get("event") == name:
            found.append(record)
    return found


# Примечание: события JSON-логов процесса по порядку
def readEvents(logFile: Path) -> list[str]:
    events = []
    for line in logFile.read_text().splitlines():
        try:
            events.append(str(json.loads(line)["event"]))
        except (ValueError, KeyError):
            continue
    return events


# Примечание: SIGTERM посреди отправки ответа — ответ уходит, marker страницы фиксируется после
# обработки (polling_stopped с marker), остановка штатная
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
        raw = uniqueText()
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
    [stopped] = readRecords(logFile, "polling_stopped")
    assert stopped["marker"] is not None, "marker не сдвинут после обработанной страницы"


# Примечание: webhook: два апдейта одного пользователя приняты (200), SIGTERM, пока первый
# «отправляется» — оба доработаны до остановки по порядку, задачи не отменены, цикла опроса нет
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
        first = uniqueText()
        second = json.loads(json.dumps(first))
        second["message"]["body"]["mid"] = f"mid.lifecycle.{uuid.uuid4().hex}"
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
    order = [r["update_id"] for r in readRecords(logFile, "update_handled")]
    mids = [str(second["message"]["body"]["mid"])]
    firstMessage = first["message"]
    assert isinstance(firstMessage, dict)
    mids.insert(0, str(firstMessage["body"]["mid"]))
    assert order == [deriveUpdateId("msg", mid) for mid in mids]
    assert events.index("bot_stopping") < events.index("max_message_sent")
    assert events.index("update_handled") < events.index("bot_stopped")
    assert "shutdown_tasks_cancelled" not in events
    assert "polling_started" not in events


# Примечание: J4: SIGTERM, пока уведомление «отправляется» (1,5 с): запрос Scheduler'а получает
# 200 delivered, сообщение ушло в MAX раньше, чем записано «доставлено», остановка штатная
def test_sigtermDuringNotification(redisContainerUrl: str, tmp_path: Path) -> None:
    port, agentPort = freePort(), freePort()
    base = f"http://127.0.0.1:{port}"
    logFile = tmp_path / "bot.log"
    token = "t" * 32
    agent = subprocess.Popen(  # noqa: S603 — своя заглушка из репо
        [sys.executable, str(AGENT_STUB)],
        env={**os.environ, "STUB_NAME": "agent", "STUB_OWNER": "C", "STUB_PORT": str(agentPort)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    env = {
        **os.environ,
        "SERVICE_TOKEN": token,
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
    notificationId = str(uuid.uuid4())
    request = json.loads((EXAMPLES / "notify" / "request.json").read_text())
    request["notification_id"] = notificationId
    responses: list[httpx.Response] = []
    try:
        waitUntil(lambda: isReady(base), 15, "ready")

        # Примечание: Scheduler ждёт ответ, пока бот «отправляет» в MAX
        def send() -> None:
            responses.append(
                httpx.post(
                    f"{base}/internal/v1/notify",
                    json=request,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15,
                )
            )

        thread = threading.Thread(target=send)
        thread.start()
        client = redis.Redis.from_url(redisContainerUrl)
        lockKey = f"va:v1:bot:notif_lock:{notificationId}"
        waitUntil(lambda: client.exists(lockKey) == 1, 5, "начало отправки")
        proc.send_signal(signal.SIGTERM)
        thread.join(timeout=20)
        proc.wait(timeout=20)
        delivered = client.get(f"va:v1:bot:notif:{notificationId}")
        client.close()
    finally:
        if proc.poll() is None:
            proc.kill()
        agent.terminate()
        agent.wait(timeout=5)
    [response] = responses
    assert response.status_code == 200, logFile.read_text()
    assert response.json()["delivered"] is True
    assert delivered is not None
    events = readEvents(logFile)
    assert events.index("max_message_sent") < events.index("notification_handled")
    assert events.index("notification_handled") < events.index("bot_stopped")
    assert "shutdown_tasks_cancelled" not in events
