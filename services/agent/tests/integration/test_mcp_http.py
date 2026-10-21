"""McpToolExecutor по настоящему Streamable HTTP против временной MCP-заглушки (deploy/stub/mcp)."""

import asyncio
import json
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import httpx
import pytest
import structlog
from pydantic import SecretStr

from agent.domain.user import Caller
from agent.infrastructure.mcp_connection import McpConnection, httpSessionFactory
from agent.infrastructure.mcp_executor import McpToolExecutor
from common.lifecycle import managedTasks
from services.agent.tests.conftest import REPO_ROOT, SERVICE_TOKEN

pytestmark = pytest.mark.integration

STUB: Final = REPO_ROOT / "deploy" / "stub" / "mcp" / "server.py"
TZ: Final = "Asia/Yekaterinburg"
START_TIMEOUT_S: Final = 15.0


# Примечание: заглушка в подпроцессе; stdout — в файл, чтобы проверить, какой caller дошёл
class StubProcess:
    # Примечание: порт фиксируем — перезапуск должен подняться по тому же адресу
    def __init__(self, port: int, logPath: Path) -> None:
        self.port = port
        self.logPath = logPath
        self.process: subprocess.Popen[bytes] | None = None

    # Примечание: URL MCP-эндпоинта
    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/mcp"

    # Примечание: запуск и ожидание /health/live
    def start(self) -> None:
        with self.logPath.open("ab") as log:
            self.process = subprocess.Popen(  # noqa: S603 — свой скрипт из репо
                [sys.executable, str(STUB)],
                env={"STUB_PORT": str(self.port), "PATH": ""},
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/health/live").status_code == 200:
                    return
            except httpx.TransportError:
                time.sleep(0.1)
        msg = f"MCP-заглушка не поднялась: {self.logPath.read_text()}"
        raise RuntimeError(msg)

    # Примечание: SIGTERM и ожидание выхода
    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            self.process.wait(timeout=10)
            self.process = None

    # Примечание: события JSON-лога заглушки
    def events(self, name: str) -> list[dict[str, object]]:
        lines = self.logPath.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines if line.startswith("{")]
        return [record for record in records if record.get("event") == name]


# Примечание: свободный порт на loopback
def freePort() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


# Примечание: заглушка на время теста
@pytest.fixture
def stub(tmp_path: Path) -> Iterator[StubProcess]:
    process = StubProcess(freePort(), tmp_path / "stub.log")
    process.start()
    try:
        yield process
    finally:
        process.stop()


# Примечание: настоящая сессия по HTTP в реестре задач, как в lifespan агента
@asynccontextmanager
async def connection(url: str) -> AsyncIterator[McpConnection]:
    async with managedTasks(timeoutS=5) as tasks:
        mcp = McpConnection(httpSessionFactory(url, SecretStr(SERVICE_TOKEN)))
        mcp.start(tasks)
        try:
            yield mcp
        finally:
            await mcp.close()


# Примечание: caller хода с уникальным trace_id
def callerFor(uniqueId: str) -> Caller:
    return Caller(userId=int(uniqueId[:8], 16), tz=TZ, traceId=uniqueId)


# Примечание: схемы по HTTP — четыре обязательных инструмента, caller вырезан
async def test_listToolsOverHttp(stub: StubProcess, uniqueId: str) -> None:
    async with connection(stub.url) as mcp:
        tools = await McpToolExecutor(mcp, callerFor(uniqueId)).listTools()
    assert {t.name for t in tools} == {"create_task", "create_event", "list_items", "search_items"}
    for tool in tools:
        assert "caller" not in json.dumps(tool.inputSchema)


# Примечание: подложенный моделью user_id не доходит до MCP — заглушка видит caller агента
async def test_callerOverwrittenOverHttp(stub: StubProcess, uniqueId: str) -> None:
    caller = callerFor(uniqueId)
    due = (datetime.now(ZoneInfo(TZ)) + timedelta(days=1)).replace(microsecond=0)
    args: dict[str, object] = {
        "title": "Позвонить Ивану",
        "kind": "reminder",
        "due_at": due.isoformat(),
        "caller": {"user_id": 1, "tz": "UTC", "trace_id": "0" * 32},
    }
    async with connection(stub.url) as mcp:
        result = await McpToolExecutor(mcp, caller).call("create_task", args)
    assert result.ok, result
    assert result.data is not None
    assert result.data["status"] == "created"
    assert "Позвонить Ивану" in result.summary
    [called] = stub.events("tool_called")
    assert called["user_id"] == caller.userId
    assert called["trace_id"] == uniqueId
    assert called["idempotency_key"] == f"{uniqueId}:1"
    assert called["tz"] == TZ


# Примечание: isError по HTTP → ok=False с кодом из конверта
async def test_pastTimeOverHttp(stub: StubProcess, uniqueId: str) -> None:
    past = (datetime.now(ZoneInfo(TZ)) - timedelta(hours=1)).isoformat()
    async with connection(stub.url) as mcp:
        result = await McpToolExecutor(mcp, callerFor(uniqueId)).call(
            "create_task", {"title": "Позвонить", "kind": "reminder", "due_at": past}
        )
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "past_time"


# Примечание: период [сегодня, завтра) в поясе теста
def todayArgs() -> dict[str, object]:
    today = datetime.now(ZoneInfo(TZ)).replace(hour=0, minute=0, second=0, microsecond=0)
    return {"from": today.isoformat(), "to": (today + timedelta(days=1)).isoformat()}


# Примечание: быстрый перезапуск MCP-сервера незаметен: следующий вызов проходит с первой попытки
async def test_serverRestart(stub: StubProcess, uniqueId: str) -> None:
    async with connection(stub.url) as mcp:
        tools = McpToolExecutor(mcp, callerFor(uniqueId))
        first = await tools.call("list_items", todayArgs())
        assert first.ok
        assert first.data is not None
        assert first.data["items"]
        stub.stop()
        stub.start()
        second = await tools.call("list_items", todayArgs())
    assert second.ok, second


# Примечание: MCP лежит во время вызова → сессия теряется, хозяин переподключается, повтор проходит
async def test_reconnectAfterOutage(stub: StubProcess, uniqueId: str) -> None:
    async with connection(stub.url) as mcp:
        tools = McpToolExecutor(mcp, callerFor(uniqueId), timeoutS=5)
        assert (await tools.call("list_items", todayArgs())).ok
        stub.stop()

        # Примечание: сервер возвращается через секунду, пока исполнитель ждёт
        async def comeBack() -> None:
            await asyncio.sleep(1.0)
            await asyncio.to_thread(stub.start)

        with structlog.testing.capture_logs() as logs:
            restarted = asyncio.create_task(comeBack())
            result = await tools.call("list_items", todayArgs())
            await restarted
    assert result.ok, result
    events = [e["event"] for e in logs]
    assert "mcp_connected" in events
    assert {"mcp_session_lost", "mcp_call_failed"} & set(events)
    keys = {e["idempotency_key"] for e in stub.events("tool_called")}
    assert f"{uniqueId}:2" in keys
