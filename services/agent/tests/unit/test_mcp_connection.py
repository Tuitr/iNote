import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Final

import mcp_types as types
import pytest
import structlog
from mcp import Client
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError

from agent.domain.user import Caller
from agent.infrastructure.mcp_connection import McpConnection, McpSession
from agent.infrastructure.mcp_executor import McpToolExecutor, McpUnavailableError
from common.lifecycle import managedTasks

TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
CALLER: Final = Caller(userId=7, tz="Europe/Moscow", traceId=TRACE)
SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "properties": {"caller": {"type": "object"}, "title": {"type": "string"}},
    "required": ["caller", "title"],
}


# Примечание: MCP-сервер в памяти (настоящий SDK): create_task с caller, past_time на «прошлое»
class MemoryServer:
    # Примечание: slowFirst — первый вызов «зависает» дольше таймаута исполнителя
    def __init__(self, slowFirst: float = 0.0) -> None:
        self.calls: list[dict[str, object]] = []
        self.slowFirst = slowFirst
        self.server = Server("memory", on_list_tools=self._listTools, on_call_tool=self._callTool)

    # Примечание: одна схема с caller
    async def _listTools(
        self, ctx: object, params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        tool = types.Tool(name="create_task", description="Создать", input_schema=SCHEMA)
        return types.ListToolsResult(tools=[tool])

    # Примечание: записывает аргументы; ответ — успех или конверт ошибки
    async def _callTool(
        self, ctx: object, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        self.calls.append(dict(params.arguments or {}))
        if self.slowFirst and len(self.calls) == 1:
            await asyncio.sleep(self.slowFirst)
        title = str((params.arguments or {}).get("title"))
        if title == "прошлое":
            envelope = {"error": {"code": "past_time", "message": "Время прошло", "details": {}}}
            return types.CallToolResult(
                content=[types.TextContent(type="text", text="Ошибка past_time")],
                structured_content=envelope,
                is_error=True,
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=f"Создано: «{title}»")],
            structured_content={"status": "created", "item": {"title": title}},
        )

    # Примечание: фабрика сессий для McpConnection — клиент SDK в памяти
    def factory(self) -> AbstractAsyncContextManager[McpSession]:
        return Client(self.server)


# Примечание: подключение в реестре задач; закрывается до реестра, как в lifespan
@asynccontextmanager
async def connected(connection: McpConnection) -> AsyncIterator[McpConnection]:
    async with managedTasks(timeoutS=2) as tasks:
        connection.start(tasks)
        try:
            yield connection
        finally:
            await connection.close()


# Примечание: content/structuredContent/isError из настоящего SDK → McpReply
async def test_sdkMapping() -> None:
    memory = MemoryServer()
    async with connected(McpConnection(memory.factory)) as connection:
        [tool] = await connection.listTools(timeoutS=2)
        assert tool.inputSchema["required"] == ["caller", "title"]
        ok = await connection.callTool("create_task", {"title": "Хлеб"}, timeoutS=2)
        assert (ok.isError, ok.text) == (False, "Создано: «Хлеб»")
        assert ok.structured == {"status": "created", "item": {"title": "Хлеб"}}
        failed = await connection.callTool("create_task", {"title": "прошлое"}, timeoutS=2)
        assert failed.isError
        assert failed.structured is not None
        assert failed.structured["error"] == {
            "code": "past_time",
            "message": "Время прошло",
            "details": {},
        }


# Примечание: исполнитель + SDK: caller подменён, past_time → код ошибки, схема без caller
async def test_executorOverSdk() -> None:
    memory = MemoryServer()
    async with connected(McpConnection(memory.factory)) as connection:
        tools = McpToolExecutor(connection, CALLER, timeoutS=2)
        [spec] = await tools.listTools()
        assert "caller" not in str(spec.inputSchema)
        result = await tools.call("create_task", {"title": "прошлое", "caller": {"user_id": 1}})
        assert result.error is not None
        assert result.error.code == "past_time"
    assert memory.calls[0]["caller"] == {
        "user_id": 7,
        "tz": "Europe/Moscow",
        "trace_id": TRACE,
        "idempotency_key": f"{TRACE}:1",
    }


# Примечание: первый вызов дольше таймаута → повтор с тем же ключом, ответ второго — пользователю
async def test_timeoutRetriedWithSameKey() -> None:
    memory = MemoryServer(slowFirst=1.0)
    async with connected(McpConnection(memory.factory)) as connection:
        result = await McpToolExecutor(connection, CALLER, timeoutS=0.2).call(
            "create_task", {"title": "Хлеб"}
        )
    assert result.ok
    keys = [call["caller"]["idempotency_key"] for call in memory.calls]  # type: ignore[index]
    assert keys == [f"{TRACE}:1", f"{TRACE}:1"]


# Примечание: сессия, которая рвётся на первом вызове (как после перезапуска MCP-сервера)
class BrokenSession:
    # Примечание: tools/list работает — кеш схем должен сброситься после переподключения
    async def list_tools(self) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[])

    # Примечание: соединение закрыто
    async def call_tool(
        self, name: str, arguments: dict[str, object] | None = None
    ) -> types.CallToolResult:
        raise MCPError(types.CONNECTION_CLOSED, "Connection closed")


# Примечание: фабрика по сценарию: сломанная сессия, потом настоящая; failConnects — отказы входа
class ScriptedFactory:
    # Примечание: сначала failConnects отказов подключения, потом broken сломанных сессий
    def __init__(self, memory: MemoryServer, *, failConnects: int = 0, broken: int = 0) -> None:
        self.memory = memory
        self.failConnects = failConnects
        self.broken = broken
        self.opened = 0

    # Примечание: очередная сессия
    def __call__(self) -> AbstractAsyncContextManager[McpSession]:
        return self._open()

    # Примечание: отказ, сломанная или живая сессия — по счётчикам
    @asynccontextmanager
    async def _open(self) -> AsyncIterator[McpSession]:
        if self.failConnects:
            self.failConnects -= 1
            raise OSError("connection refused")
        self.opened += 1
        if self.broken:
            self.broken -= 1
            yield BrokenSession()
            return
        async with Client(self.memory.server) as client:
            yield client


# Примечание: обрыв сессии → переподключение, повтор того же вызова, кеш схем от новой сессии
async def test_reconnectAfterConnectionClosed() -> None:
    memory = MemoryServer()
    factory = ScriptedFactory(memory, broken=1)
    async with connected(McpConnection(factory)) as connection:
        assert await connection.listTools(timeoutS=2) == []
        result = await McpToolExecutor(connection, CALLER, timeoutS=2).call(
            "create_task", {"title": "Хлеб"}
        )
        assert result.ok
        assert factory.opened == 2
        [tool] = await connection.listTools(timeoutS=2)
        assert tool.name == "create_task"
    assert len(memory.calls) == 1


# Примечание: состояние сессии сейчас (функция — чтобы mypy не сужал тип между проверками)
def isUp(connection: McpConnection) -> bool:
    return connection.isConnected


# Примечание: MCP лежит на старте — вызов быстро получает unavailable, потом сессия поднимается
async def test_unavailableUntilConnected() -> None:
    factory = ScriptedFactory(MemoryServer(), failConnects=1_000)
    with structlog.testing.capture_logs() as logs:
        async with connected(McpConnection(factory)) as connection:
            with pytest.raises(McpUnavailableError):
                await connection.callTool("create_task", {"title": "Хлеб"}, timeoutS=0.1)
            assert not isUp(connection)
            factory.failConnects = 0
            reply = await connection.callTool("create_task", {"title": "Хлеб"}, timeoutS=6)
            assert not reply.isError
            assert isUp(connection)
    [failed] = [e for e in logs if e["event"] == "mcp_connect_failed"]
    assert failed["error"] == "OSError"


# Примечание: close() во время паузы между попытками — без ожидания таймаута реестра
async def test_closeWhileReconnecting() -> None:
    factory = ScriptedFactory(MemoryServer(), failConnects=1_000)
    loop = asyncio.get_running_loop()
    started = loop.time()
    async with managedTasks(timeoutS=2) as tasks:
        connection = McpConnection(factory)
        connection.start(tasks)
        await asyncio.sleep(0.05)
        await connection.close()
        assert tasks.activeCount == 0
    assert loop.time() - started < 1
    with pytest.raises(McpUnavailableError):
        await connection.callTool("create_task", {}, timeoutS=0.1)


# Примечание: без start() вызов не висит — unavailable по таймауту ожидания
async def test_notStarted() -> None:
    connection = McpConnection(MemoryServer().factory)
    with pytest.raises(McpUnavailableError):
        await connection.callTool("create_task", {}, timeoutS=0.05)
    await connection.close()
