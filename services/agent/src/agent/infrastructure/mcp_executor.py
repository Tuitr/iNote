"""McpToolExecutor (J3): инструменты MCP для Brain; caller заполняет агент, а не модель.

Здесь нет SDK: транспорт — порт McpChannel (реализация — McpConnection в mcp_connection.py),
поэтому правила caller, ключей и повторов проверяются unit-тестами без сети.
"""

import copy
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from agent.domain.user import Caller
from common.errors import ErrorCode
from common.logging import getLogger
from contracts.agent_side import ErrorInfo, JsonObject, ToolExecutor, ToolResult, ToolSpec

CALLER_FIELD: Final = "caller"
# Стандарт: MCP — 5 с и 1 повтор, только с ключом идемпотентности; ключ есть у каждого вызова
DEFAULT_TIMEOUT_S: Final = 5.0
RETRIES: Final = 1
TOOL_FAILED: Final = "Ошибка инструмента"

_log = getLogger(__name__)


# Примечание: MCP недоступен: нет соединения или оно оборвалось во время вызова
class McpUnavailableError(Exception):
    pass


# Примечание: ответ tools/call без SDK: content[0].text, structuredContent, isError
@dataclass(frozen=True)
class McpReply:
    text: str
    structured: JsonObject | None
    isError: bool


# Примечание: порт транспорта: TimeoutError — не уложились, McpUnavailableError — нет связи
class McpChannel(Protocol):
    # Примечание: схемы как их отдаёт сервер — с caller; кеш на время жизни сессии
    async def listTools(self, timeoutS: float) -> list[ToolSpec]: ...

    # Примечание: один вызов tools/call; при обрыве канал сам переподключается к следующему
    async def callTool(self, name: str, arguments: JsonObject, timeoutS: float) -> McpReply: ...


# Примечание: исполнитель на один ход: свой счётчик вызовов → ключи <trace_id>:<n> (правило 6)
class McpToolExecutor:
    # Примечание: caller — из UserContext и trace хода, модель на него не влияет
    def __init__(
        self,
        channel: McpChannel,
        caller: Caller,
        *,
        timeoutS: float = DEFAULT_TIMEOUT_S,
        retries: int = RETRIES,
    ) -> None:
        self._channel = channel
        self._caller = caller
        self._timeoutS = timeoutS
        self._retries = retries
        self._calls = 0

    # Примечание: схемы для LLM — без caller (J3); при недоступности MCP — пустой список
    async def listTools(self) -> list[ToolSpec]:
        for attempt in range(self._retries + 1):
            try:
                tools = await self._channel.listTools(self._timeoutS)
            except (TimeoutError, McpUnavailableError) as exc:
                _log.warning("mcp_list_tools_failed", attempt=attempt + 1, error=type(exc).__name__)
                continue
            return [withoutCaller(tool) for tool in tools]
        return []

    # Примечание: копия args без caller модели + свой caller; таймаут и обрыв → один повтор
    async def call(self, name: str, args: Mapping[str, object]) -> ToolResult:
        self._calls += 1
        key = f"{self._caller.traceId}:{self._calls}"
        arguments: JsonObject = {k: v for k, v in args.items() if k != CALLER_FIELD}
        if CALLER_FIELD in args:
            # Значения не логируем: подложенный моделью caller — только факт (правило 1)
            _log.warning("mcp_caller_overridden", tool=name)
        arguments[CALLER_FIELD] = {
            "user_id": self._caller.userId,
            "tz": self._caller.tz,
            "trace_id": self._caller.traceId,
            "idempotency_key": key,
        }
        code = ErrorCode.UPSTREAM_UNAVAILABLE
        for attempt in range(self._retries + 1):
            try:
                reply = await self._channel.callTool(name, arguments, self._timeoutS)
            except TimeoutError:
                code = ErrorCode.UPSTREAM_TIMEOUT
            except McpUnavailableError:
                code = ErrorCode.UPSTREAM_UNAVAILABLE
            else:
                result = toToolResult(reply)
                _log.info(
                    "mcp_tool_called",
                    tool=name,
                    idempotency_key=key,
                    ok=result.ok,
                    error_code=result.error.code if result.error else None,
                    attempt=attempt + 1,
                )
                return result
            _log.warning("mcp_call_failed", tool=name, idempotency_key=key, error_code=code)
        return _failure(code, f"MCP: {code}")


# Примечание: новый исполнитель на каждый ход — счётчик ключей начинается с 1
class McpToolExecutorFactory:
    # Примечание: канал — один на процесс, создаётся в lifespan (правило 9)
    def __init__(self, channel: McpChannel, *, timeoutS: float = DEFAULT_TIMEOUT_S) -> None:
        self._channel = channel
        self._timeoutS = timeoutS

    # Примечание: исполнитель с caller этого хода
    def forTurn(self, caller: Caller) -> ToolExecutor:
        return McpToolExecutor(self._channel, caller, timeoutS=self._timeoutS)


# Примечание: content[0].text → summary, structuredContent → data, isError → ok=False и конверт
def toToolResult(reply: McpReply) -> ToolResult:
    if not reply.isError:
        return ToolResult(ok=True, summary=reply.text, data=reply.structured)
    envelope = (reply.structured or {}).get("error")
    if isinstance(envelope, dict) and isinstance(envelope.get("code"), str):
        details = envelope.get("details")
        error = ErrorInfo(
            code=envelope["code"],
            message=str(envelope.get("message") or reply.text or TOOL_FAILED),
            details=details if isinstance(details, dict) else {},
        )
    else:
        # Ошибка без конверта (сбой SDK, неизвестный инструмент) — internal с текстом сервера
        error = ErrorInfo(code=ErrorCode.INTERNAL, message=reply.text or TOOL_FAILED)
    return ToolResult(ok=False, summary=reply.text or f"Ошибка {error.code}", error=error)


# Примечание: схема без caller: из properties и required, плюс ставшие ненужными $defs
def withoutCaller(tool: ToolSpec) -> ToolSpec:
    schema = copy.deepcopy(tool.inputSchema)
    properties = schema.get("properties")
    if isinstance(properties, dict):
        properties.pop(CALLER_FIELD, None)
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [field for field in required if field != CALLER_FIELD]
    _dropUnusedDefs(schema)
    return tool.model_copy(update={"inputSchema": schema})


# Примечание: определения из $defs, на которые больше никто не ссылается (Caller у pydantic-схем)
def _dropUnusedDefs(schema: JsonObject) -> None:
    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        return
    while True:
        body = json.dumps({k: v for k, v in schema.items() if k != "$defs"})
        unused = [name for name in defs if not _isReferenced(name, body, defs)]
        if not unused:
            break
        for name in unused:
            del defs[name]
    if not defs:
        del schema["$defs"]


# Примечание: на определение ссылается тело схемы или другое определение
def _isReferenced(name: str, body: str, defs: dict[str, object]) -> bool:
    ref = f'"#/$defs/{name}"'
    others = json.dumps({other: value for other, value in defs.items() if other != name})
    return ref in body or ref in others


# Примечание: ошибка транспорта в форме ToolResult — Brain решает, что сказать пользователю
def _failure(code: str, message: str) -> ToolResult:
    return ToolResult(
        ok=False,
        summary=f"Ошибка {code}: сервис записей не ответил",
        error=ErrorInfo(code=code, message=message),
    )
