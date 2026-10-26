"""Lifecycle бота с соседями (S4, шаг 2).

1. SIGTERM, пока ASR распознаёт голосовое (1,5 с): апдейт доработан, пользователь получил ответ
   (агент-заглушка отвечает ошибкой → текст бота с кодом), задачи не отменены.
2. Старт без Redis и без агента: live 200, ready 503 со списком проверок (ASR в readiness нет и
   не вызывается); соседи поднялись — ready 200 без перезапуска.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final, override

import httpx
import pytest

from common.testing.pytest_plugin import redisOnPort

pytestmark = [pytest.mark.lifecycle, pytest.mark.integration]

REPO_ROOT: Final = Path(__file__).resolve().parents[4]
AGENT_STUB: Final = REPO_ROOT / "deploy" / "stub" / "server.py"
TOKEN: Final = "t" * 32
ASR_DELAY_S: Final = 1.5
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


# Примечание: события JSON-лога по порядку
def events(logFile: Path) -> list[str]:
    lines = logFile.read_text(encoding="utf-8").splitlines()
    return [str(json.loads(line).get("event")) for line in lines if line.startswith("{")]


# Примечание: «медленный ASR» по J1: ждёт delayS и отвечает распознанным текстом; calls — запросы
class SlowAsr:
    # Примечание: started — первый запрос пришёл (бот уже ждёт ASR)
    def __init__(self, delayS: float) -> None:
        self.delayS = delayS
        self.calls: list[str] = []
        self.started = threading.Event()

    # Примечание: HTTP-сервер в потоке на заданном порту
    @contextmanager
    def serve(self, port: int) -> Iterator[None]:
        owner = self

        # Примечание: обработчик запросов stdlib-сервера
        class Handler(BaseHTTPRequestHandler):
            # Примечание: POST /v1/transcribe → через delayS ответ J1
            def do_POST(self) -> None:
                owner.calls.append(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                owner.started.set()
                time.sleep(owner.delayS)
                body = json.dumps(
                    {
                        "text": "что у меня завтра",
                        "raw_text": "что у меня завтра",
                        "confidence": 0.95,
                        "low_confidence": False,
                        "duration_ms": 1200,
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            # Примечание: GET — тоже запрос к ASR (readiness не должна его делать)
            def do_GET(self) -> None:
                owner.calls.append(self.path)
                self.send_response(200)
                self.end_headers()

            # Примечание: без логов stdlib в stderr
            @override
            def log_message(self, format: str, *args: object) -> None:
                return

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield
        finally:
            server.shutdown()
            server.server_close()


# Примечание: stdlib-заглушка агента (health 200, POST → 404) на заданном порту
@contextmanager
def agentStub(port: int) -> Iterator[None]:
    env = {**os.environ, "STUB_NAME": "agent", "STUB_OWNER": "C", "STUB_PORT": str(port)}
    proc = subprocess.Popen(  # noqa: S603 — своя заглушка из репо
        [sys.executable, str(AGENT_STUB)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        waitUntil(lambda: isLive(f"http://127.0.0.1:{port}"), 10, "агент-заглушка")
        yield
    finally:
        proc.terminate()
        proc.wait(timeout=5)


# Примечание: бот под uvicorn как в образе (фейковый MAX, polling)
@contextmanager
def botProcess(port: int, env: dict[str, str], logFile: Path) -> Iterator[subprocess.Popen[bytes]]:
    command = [sys.executable, "-m", "uvicorn", "bot.main:createApp", "--factory"]
    options = ["--host", "127.0.0.1", "--port", str(port)]
    with logFile.open("w") as out:
        proc = subprocess.Popen(  # noqa: S603 — свой сервис текущим интерпретатором
            [*command, *options, "--timeout-graceful-shutdown", str(GRACEFUL_S)],
            stdout=out,
            stderr=subprocess.STDOUT,
            env={**os.environ, "SERVICE_TOKEN": TOKEN, "LOG_FORMAT": "json", **env},
        )
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


# Примечание: SIGTERM, пока ASR распознаёт: апдейт доработан, ответ ушёл в MAX до остановки
def test_sigtermDuringAsr(redisContainerUrl: str, tmp_path: Path) -> None:
    port, agentPort, asrPort = freePort(), freePort(), freePort()
    base, logFile = f"http://127.0.0.1:{port}", tmp_path / "bot.log"
    asr = SlowAsr(ASR_DELAY_S)
    env = {
        "REDIS_URL": redisContainerUrl,
        "BOT_MAX_MODE": "fake",
        "BOT_POLL_TIMEOUT_S": "1",
        "BOT_SHUTDOWN_TIMEOUT_S": "10",
        "BOT_AGENT_URL": f"http://127.0.0.1:{agentPort}",
        "BOT_ASR_URL": f"http://127.0.0.1:{asrPort}",
        "BOT_FAKE_FILES_URL": base,
    }
    with agentStub(agentPort), asr.serve(asrPort), botProcess(port, env, logFile) as proc:
        waitUntil(lambda: (r := ready(base)) is not None and r.status_code == 200, 15, "ready")
        chat = 900_000_000 + int(time.time_ns() % 100_000_000)
        voice = {"max_user_id": chat, "max_chat_id": chat, "text": "что у меня завтра"}
        httpx.post(f"{base}/dev/max/voice", json=voice, timeout=2).raise_for_status()
        assert asr.started.wait(10), logFile.read_text()
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
    seen = events(logFile)
    assert proc.returncode in {0, -signal.SIGTERM}, logFile.read_text()
    assert seen.index("bot_stopping") < seen.index("max_message_sent")
    assert seen.index("max_message_sent") < seen.index("update_handled")
    assert seen.index("update_handled") < seen.index("bot_stopped")
    assert "shutdown_tasks_cancelled" not in seen


# Примечание: старт без Redis и агента: живой, ready 503 (redis и agent — fail, ASR в проверках
# нет и не вызывается); Redis и агент поднялись — ready 200 тем же процессом
def test_startsWithoutNeighbours(tmp_path: Path) -> None:
    port, agentPort, asrPort, redisPort = freePort(), freePort(), freePort(), freePort()
    base = f"http://127.0.0.1:{port}"
    asr = SlowAsr(0)
    env = {
        "REDIS_URL": f"redis://127.0.0.1:{redisPort}/0",
        "BOT_MAX_MODE": "fake",
        "BOT_POLL_TIMEOUT_S": "1",
        "BOT_AGENT_URL": f"http://127.0.0.1:{agentPort}",
        "BOT_ASR_URL": f"http://127.0.0.1:{asrPort}",
    }
    with asr.serve(asrPort), botProcess(port, env, tmp_path / "bot.log") as proc:
        waitUntil(lambda: isLive(base), 15, "live")
        first = ready(base)
        assert first is not None
        assert first.status_code == 503
        assert first.json()["checks"] == {"redis": "fail", "agent": "fail"}
        with agentStub(agentPort), redisOnPort(redisPort):
            waitUntil(lambda: (r := ready(base)) is not None and r.status_code == 200, 30, "ready")
        assert proc.poll() is None  # тот же процесс
    assert asr.calls == []  # readiness не зовёт ASR (правило 9)
