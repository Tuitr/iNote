"""Одна MCP-сессия на процесс (правило 9): Streamable HTTP на AGENT_MCP_URL, официальный SDK.

Сессию держит фоновая задача-«хозяин»: группа задач SDK (anyio) должна открываться и закрываться
в одной задаче, а падение транспорта отменяет именно её, а не запросы пользователей.
Обрыв → вызов получает McpUnavailableError, хозяин переподключается, исполнитель повторяет вызов.
"""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Final, Protocol

import anyio
import httpx2
import mcp_types
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from pydantic import SecretStr

from agent.infrastructure.mcp_executor import McpReply, McpUnavailableError
from common.lifecycle import TaskRegistry
from common.logging import getLogger
from contracts.agent_side import JsonObject, ToolSpec

CONNECT_TIMEOUT_S: Final = 5.0
# SSE-поток сервер может держать долго; таймаут вызова задаёт исполнитель, а не HTTP-клиент
SSE_READ_TIMEOUT_S: Final = 300.0
RECONNECT_MIN_DELAY_S: Final = 0.2
RECONNECT_MAX_DELAY_S: Final = 5.0
CLOSE_TIMEOUT_S: Final = 5.0
TASK_NAME: Final = "mcp_session"

_log = getLogger(__name__)
# Сбои связи: SDK закрыл соединение, httpx2 не достучался, поток уже закрыт
_TRANSPORT_ERRORS: Final = (
    httpx2.TransportError,
    anyio.ClosedResourceError,
    anyio.BrokenResourceError,
)


# Примечание: то, что нужно от mcp.Client — для подмены в тестах
class McpSession(Protocol):
    # Примечание: список инструментов сервера
    async def list_tools(self) -> mcp_types.ListToolsResult: ...

    # Примечание: вызов инструмента
    async def call_tool(
        self, name: str, arguments: dict[str, object] | None = None
    ) -> mcp_types.CallToolResult: ...


type SessionFactory = Callable[[], AbstractAsyncContextManager[McpSession]]


# Примечание: клиент SDK поверх Streamable HTTP; сервисный токен — в заголовке, как у всех вызовов
def httpSessionFactory(url: str, serviceToken: SecretStr | None) -> SessionFactory:
    headers = {"Authorization": f"Bearer {serviceToken.get_secret_value()}"} if serviceToken else {}

    # Примечание: новый HTTP-клиент на каждую сессию: после обрыва старый может быть сломан
    def create() -> AbstractAsyncContextManager[McpSession]:
        http = httpx2.AsyncClient(
            headers=headers,
            timeout=httpx2.Timeout(CONNECT_TIMEOUT_S, read=SSE_READ_TIMEOUT_S),
        )
        return _OwnedClient(Client(streamable_http_client(url, http_client=http)), http)

    return create


# Примечание: mcp.Client вместе со своим HTTP-клиентом: закрываются вместе
class _OwnedClient:
    # Примечание: SDK не закрывает переданный ему http_client — это делаем мы
    def __init__(self, client: Client, http: httpx2.AsyncClient) -> None:
        self._client = client
        self._http = http

    # Примечание: открыть сессию MCP (initialize)
    async def __aenter__(self) -> McpSession:
        try:
            return await self._client.__aenter__()
        except BaseException:
            await self._http.aclose()
            raise

    # Примечание: закрыть сессию, затем HTTP-клиент
    async def __aexit__(self, *excInfo: object) -> None:
        try:
            await self._client.__aexit__(None, None, None)
        finally:
            await self._http.aclose()


# Примечание: McpChannel на SDK: хозяин сессии, переподключение, кеш схем на сессию
class McpConnection:
    # Примечание: factory — откуда брать сессию (HTTP в проде, in-memory/фейк в тестах)
    def __init__(self, factory: SessionFactory) -> None:
        self._factory = factory
        self._session: McpSession | None = None
        self._tools: list[ToolSpec] | None = None
        self._ready = asyncio.Event()
        self._broken = asyncio.Event()
        self._stopping = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    # Примечание: True, пока сессия открыта (для readiness — шаг 4)
    @property
    def isConnected(self) -> bool:
        return self._ready.is_set()

    # Примечание: запустить хозяина сессии в реестре фоновых задач; подключение — в фоне
    def start(self, registry: TaskRegistry) -> None:
        self._task = registry.spawn(self._own(), TASK_NAME)

    # Примечание: закрыть сессию до остановки реестра задач
    async def close(self) -> None:
        self._stopping.set()
        self._broken.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(self._task), CLOSE_TIMEOUT_S)
        except TimeoutError:
            self._task.cancel()
            _log.warning("mcp_close_timeout")

    # Примечание: схемы с caller как есть; кеш сбрасывается при новой сессии
    async def listTools(self, timeoutS: float) -> list[ToolSpec]:
        if self._tools is not None:
            return self._tools
        session = await self._current(timeoutS)
        async with asyncio.timeout(timeoutS):
            try:
                listed = await session.list_tools()
            except (MCPError, *_TRANSPORT_ERRORS) as exc:
                raise self._lost(session, exc) from exc
        tools = [
            ToolSpec(name=t.name, description=t.description or "", inputSchema=dict(t.input_schema))
            for t in listed.tools
        ]
        if session is self._session:
            self._tools = tools
        return tools

    # Примечание: сессию ждём до timeoutS, сам вызов — тоже; обрыв → McpUnavailableError
    async def callTool(self, name: str, arguments: JsonObject, timeoutS: float) -> McpReply:
        session = await self._current(timeoutS)
        async with asyncio.timeout(timeoutS):
            try:
                result = await session.call_tool(name, arguments)
            except MCPError as exc:
                if exc.code != mcp_types.CONNECTION_CLOSED:
                    # Протокольная ошибка — связь цела, повтор не поможет
                    _log.warning("mcp_protocol_error", tool=name, mcp_code=exc.code)
                    return McpReply(text=exc.message, structured=None, isError=True)
                raise self._lost(session, exc) from exc
            except _TRANSPORT_ERRORS as exc:
                raise self._lost(session, exc) from exc
        text = next(
            (block.text for block in result.content if isinstance(block, mcp_types.TextContent)), ""
        )
        structured = dict(result.structured_content) if result.structured_content else None
        return McpReply(text=text, structured=structured, isError=bool(result.is_error))

    # Примечание: открытая сессия; ждём хозяина не дольше бюджета вызова
    async def _current(self, waitS: float) -> McpSession:
        if self._stopping.is_set():
            raise McpUnavailableError("агент останавливается")
        if not self._ready.is_set():
            try:
                async with asyncio.timeout(waitS):
                    await self._ready.wait()
            except TimeoutError:
                raise McpUnavailableError("нет соединения с MCP") from None
        session = self._session
        if session is None:
            raise McpUnavailableError("нет соединения с MCP")
        return session

    # Примечание: сессия сломана — хозяин откроет новую; вызывающему — McpUnavailableError
    def _lost(self, session: McpSession, exc: BaseException) -> McpUnavailableError:
        if session is self._session:
            self._ready.clear()
            self._broken.set()
        _log.warning("mcp_session_lost", error=_rootName(exc))
        return McpUnavailableError(str(exc) or type(exc).__name__)

    # Примечание: хозяин: открыть сессию, ждать обрыва или остановки, переподключиться с паузой
    async def _own(self) -> None:
        delay = RECONNECT_MIN_DELAY_S
        failures = 0
        while not self._stopping.is_set():
            try:
                async with self._factory() as session:
                    self._session, self._tools = session, None
                    self._broken.clear()
                    self._ready.set()
                    _log.info("mcp_connected", after_failures=failures)
                    delay, failures = RECONNECT_MIN_DELAY_S, 0
                    # close() мог прийти, пока шло подключение: тогда сразу выходим
                    if not self._stopping.is_set():
                        await self._broken.wait()
            except Exception as exc:  # noqa: BLE001 — любой сбой сессии лечится переподключением
                failures += 1
                # Первый сбой — warning, дальше debug: MCP может лежать долго, лог не засоряем
                logMethod = _log.warning if failures == 1 else _log.debug
                logMethod("mcp_connect_failed", error=_rootName(exc), failures=failures)
            finally:
                self._session = None
                self._ready.clear()
            if self._stopping.is_set():
                break
            try:
                await asyncio.wait_for(self._stopping.wait(), delay)
            except TimeoutError:
                delay = min(delay * 2, RECONNECT_MAX_DELAY_S)
        _log.info("mcp_closed")


# Примечание: anyio заворачивает сбой транспорта в ExceptionGroup — в лог идёт первопричина
def _rootName(exc: BaseException) -> str:
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return type(exc).__name__
