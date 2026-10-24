"""Lifecycle ASR: настоящий uvicorn в подпроцессе (как в контейнере).

1. Старт без Redis: live 200, ready 503 (`redis`); распознавание при этом работает (кеш — не
   источник правды); Redis поднялся — ready 200 без перезапуска.
2. SIGTERM во время распознавания: запрос, который уже идёт к провайдеру, доработан и получил
   ответ; процесс штатно вышел (0 или -SIGTERM у uvicorn) и записал `asr_stopped`.
Провайдер — локальный HTTP-сервер по протоколу SpeechKit v1 (без сети и денег).
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
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final, override

import httpx
import pytest

from common.testing.pytest_plugin import redisOnPort
from services.asr.tests.oggdata import MONO_2500MS, fixture

pytestmark = [pytest.mark.lifecycle, pytest.mark.integration]

TOKEN: Final = "t" * 32
PROVIDER_DELAY_S: Final = 1.5
GRACEFUL_S: Final = 15


# Примечание: свободный порт на loopback
def freePort() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


# Примечание: ждём условие; не дождались — падаем с пояснением
def waitUntil(predicate: Callable[[], bool], timeoutS: float, what: str) -> None:
    deadline = time.monotonic() + timeoutS
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    pytest.fail(f"не дождались: {what}")


# Примечание: статус /health/ready (None — не отвечает)
def readyStatus(base: str) -> int | None:
    try:
        return httpx.get(f"{base}/health/ready", timeout=2).status_code
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


# Примечание: «SpeechKit» на loopback: ждёт delayS и отвечает {"result": …}; started — запрос пришёл
class SlowSpeechKit:
    # Примечание: calls — пути запросов
    def __init__(self, delayS: float) -> None:
        self.delayS = delayS
        self.calls: list[str] = []
        self.started = threading.Event()

    # Примечание: HTTP-сервер в потоке
    @contextmanager
    def serve(self, port: int) -> Iterator[None]:
        owner = self

        # Примечание: обработчик stdlib-сервера
        class Handler(BaseHTTPRequestHandler):
            # Примечание: POST stt:recognize → через delayS текст
            def do_POST(self) -> None:
                owner.calls.append(self.path)
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                owner.started.set()
                time.sleep(owner.delayS)
                body = json.dumps({"result": "что у меня завтра"}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            # Примечание: без логов stdlib
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


# Примечание: uvicorn asr.main:createApp в подпроцессе с JSON-логом в файл
@contextmanager
def runAsr(
    tmpPath: Path, env: dict[str, str]
) -> Iterator[tuple[str, subprocess.Popen[bytes], Path]]:
    port = freePort()
    logFile = tmpPath / "asr.log"
    fullEnv = {
        "PATH": os.environ.get("PATH", ""),
        "SERVICE_TOKEN": TOKEN,
        "LOG_FORMAT": "json",
        **env,
    }
    with logFile.open("wb") as out:
        process = subprocess.Popen(  # noqa: S603 — команда собрана из констант теста
            [
                sys.executable,
                "-m",
                "uvicorn",
                "asr.main:createApp",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--timeout-graceful-shutdown",
                str(GRACEFUL_S),
            ],
            env=fullEnv,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
        base = f"http://127.0.0.1:{port}"
        try:
            waitUntil(lambda: isLive(base), 20, "asr /health/live")
            yield base, process, logFile
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(10)


# Примечание: POST /v1/transcribe как у бота
def transcribe(base: str, audio: bytes) -> httpx.Response:
    return httpx.post(
        f"{base}/v1/transcribe",
        files={"audio": ("voice.ogg", audio, "audio/ogg")},
        data={"lang": "ru-RU"},
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=20,
    )


# Примечание: старт без Redis — не готов, но распознаёт; Redis поднялся — готов без перезапуска
def test_startWithoutRedis(tmp_path: Path) -> None:
    redisPort = freePort()
    with runAsr(
        tmp_path, {"REDIS_URL": f"redis://127.0.0.1:{redisPort}/0", "ASR_PROVIDER": "fake"}
    ) as (base, _, _):
        assert readyStatus(base) == 503
        response = transcribe(base, "OggSчто у меня сегодня".encode())
        assert response.status_code == 200
        assert response.json()["text"] == "что у меня сегодня"
        with redisOnPort(redisPort):
            waitUntil(lambda: readyStatus(base) == 200, 20, "ready 200 после Redis")


# Примечание: SIGTERM во время запроса к провайдеру: ответ доставлен, штатный выход, asr_stopped
def test_sigtermDuringRecognition(tmp_path: Path, redisContainerUrl: str) -> None:
    provider = SlowSpeechKit(PROVIDER_DELAY_S)
    providerPort = freePort()
    env = {
        "REDIS_URL": redisContainerUrl,
        "ASR_PROVIDER": "yandex",
        "ASR_YANDEX_API_KEY": "test-key",
        "ASR_YANDEX_URL": f"http://127.0.0.1:{providerPort}/speech/v1/stt:recognize",
    }
    with (
        provider.serve(providerPort),
        runAsr(tmp_path, env) as (base, process, logFile),
        ThreadPoolExecutor(max_workers=1) as pool,
    ):
        inFlight: Future[httpx.Response] = pool.submit(transcribe, base, fixture(MONO_2500MS))
        assert provider.started.wait(10), "запрос не дошёл до провайдера"
        process.send_signal(signal.SIGTERM)
        response = inFlight.result(timeout=20)
        process.wait(GRACEFUL_S + 5)

    # uvicorn после штатной остановки заново поднимает SIGTERM (код -15) — это норма
    assert process.returncode in {0, -signal.SIGTERM}, logFile.read_text()

    assert response.status_code == 200
    assert response.json()["text"] == "Что у меня завтра"
    assert response.json()["duration_ms"] == 2500
    assert len(provider.calls) == 1
    logged = events(logFile)
    assert logged.index("asr_stopping") < logged.index("asr_stopped")
