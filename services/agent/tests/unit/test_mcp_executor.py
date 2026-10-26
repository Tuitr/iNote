from collections.abc import Callable
from typing import Final, override

import pytest
import structlog

from agent.domain.user import Caller
from agent.infrastructure.mcp_executor import (
    McpReply,
    McpToolExecutor,
    McpToolExecutorFactory,
    McpUnavailableError,
    toToolResult,
    withoutCaller,
)
from contracts.agent_side import JsonObject, ToolSpec
from services.agent.tests.fakes import ITEM

TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
CALLER: Final = Caller(userId=42, tz="Asia/Yekaterinburg", traceId=TRACE)
OK: Final = McpReply(text="Создано", structured={"status": "created", "item": ITEM}, isError=False)
CALLER_SCHEMA: Final[JsonObject] = {
    "type": "object",
    "properties": {"user_id": {"type": "integer"}},
    "required": ["user_id"],
}

type Step = McpReply | BaseException


# Примечание: канал по сценарию: каждый вызов берёт следующий шаг (ответ или исключение)
class FakeChannel:
    # Примечание: steps — по одному на вызов callTool; tools — что вернёт listTools
    def __init__(self, *steps: Step, tools: list[ToolSpec] | None = None) -> None:
        self.steps = list(steps)
        self.tools = tools or []
        self.calls: list[tuple[str, JsonObject, float]] = []
        self.listed = 0

    # Примечание: схемы с caller, как их отдаёт сервер
    async def listTools(self, timeoutS: float) -> list[ToolSpec]:
        self.listed += 1
        return self.tools

    # Примечание: записывает аргументы и отдаёт следующий шаг
    async def callTool(self, name: str, arguments: JsonObject, timeoutS: float) -> McpReply:
        self.calls.append((name, arguments, timeoutS))
        step = self.steps.pop(0) if self.steps else OK
        if isinstance(step, BaseException):
            raise step
        return step

    # Примечание: caller n-го вызова
    def caller(self, index: int) -> JsonObject:
        raw = self.calls[index][1]["caller"]
        assert isinstance(raw, dict)
        return raw


# Примечание: исполнитель над фейковым каналом
def executor(channel: FakeChannel) -> McpToolExecutor:
    return McpToolExecutor(channel, CALLER, timeoutS=5.0)


# Примечание: правило 1 — caller, подложенный моделью, выброшен и заменён своим
async def test_modelCallerIsOverwritten() -> None:
    channel = FakeChannel()
    fake = {"user_id": 999, "tz": "UTC", "trace_id": "x" * 32, "idempotency_key": "evil:1"}
    with structlog.testing.capture_logs() as logs:
        await executor(channel).call("create_task", {"title": "Позвонить", "caller": fake})
    caller = channel.caller(0)
    assert caller == {
        "user_id": 42,
        "tz": "Asia/Yekaterinburg",
        "trace_id": TRACE,
        "idempotency_key": f"{TRACE}:1",
    }
    assert channel.calls[0][1]["title"] == "Позвонить"
    assert any(e["event"] == "mcp_caller_overridden" for e in logs)
    assert "999" not in str(logs)


# Примечание: args модели не меняются — исполнитель работает с копией
async def test_argsAreCopied() -> None:
    args: dict[str, object] = {"title": "Позвонить", "caller": {"user_id": 1}}
    await executor(FakeChannel()).call("create_task", args)
    assert args == {"title": "Позвонить", "caller": {"user_id": 1}}


# Примечание: ключи <trace_id>:<n> по порядку вызовов в ходе; новый ход — снова с 1
async def test_idempotencyKeysCountPerTurn() -> None:
    channel = FakeChannel()
    factory = McpToolExecutorFactory(channel)
    first = factory.forTurn(CALLER)
    await first.call("list_items", {})
    await first.call("create_task", {"title": "a"})
    await factory.forTurn(CALLER).call("create_task", {"title": "b"})
    keys = [channel.caller(i)["idempotency_key"] for i in range(3)]
    assert keys == [f"{TRACE}:1", f"{TRACE}:2", f"{TRACE}:1"]


# Примечание: таймаут или обрыв → один повтор с тем же ключом, успех второго вызова — успех
@pytest.mark.parametrize("failure", [TimeoutError(), McpUnavailableError("обрыв")])
async def test_retryKeepsKey(failure: BaseException) -> None:
    channel = FakeChannel(failure, OK)
    result = await executor(channel).call("create_task", {"title": "a"})
    assert result.ok
    assert len(channel.calls) == 2
    assert channel.caller(0)["idempotency_key"] == channel.caller(1)["idempotency_key"]
    assert channel.calls[0][2] == 5.0


# Примечание: обе попытки неудачны → ToolResult с кодом стандарта, без исключения наружу
@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (TimeoutError, "upstream_timeout"),
        (lambda: McpUnavailableError("нет связи"), "upstream_unavailable"),
    ],
)
async def test_retriesExhausted(failure: Callable[[], BaseException], code: str) -> None:
    channel = FakeChannel(failure(), failure())
    result = await executor(channel).call("create_task", {"title": "a"})
    assert not result.ok
    assert result.error is not None
    assert result.error.code == code
    assert len(channel.calls) == 2


# Примечание: isError + конверт → ok=False с кодом, сообщением и details; повтора нет
async def test_isErrorMapsEnvelope() -> None:
    reply = McpReply(
        text="Ошибка past_time: время уже прошло",
        structured={
            "error": {"code": "past_time", "message": "Время уже прошло", "details": {"a": 1}}
        },
        isError=True,
    )
    channel = FakeChannel(reply)
    result = await executor(channel).call("create_task", {"title": "a"})
    assert len(channel.calls) == 1
    assert not result.ok
    assert result.summary == "Ошибка past_time: время уже прошло"
    assert result.error is not None
    assert (result.error.code, result.error.message, result.error.details) == (
        "past_time",
        "Время уже прошло",
        {"a": 1},
    )


# Примечание: isError без конверта (сбой SDK, неизвестный инструмент) → internal с текстом сервера
def test_isErrorWithoutEnvelope() -> None:
    result = toToolResult(McpReply(text="Unknown tool: nope", structured=None, isError=True))
    assert result.error is not None
    assert (result.error.code, result.error.message) == ("internal", "Unknown tool: nope")


# Примечание: успех: content[0].text → summary, structuredContent → data
def test_successMapping() -> None:
    result = toToolResult(OK)
    assert (result.ok, result.summary, result.data) == (True, "Создано", OK.structured)


# Примечание: схема для LLM без caller — ни в properties, ни в required
async def test_listToolsWithoutCaller() -> None:
    spec = ToolSpec(
        name="create_task",
        description="Создать задачу",
        inputSchema={
            "type": "object",
            "properties": {"caller": CALLER_SCHEMA, "title": {"type": "string"}},
            "required": ["caller", "title"],
        },
    )
    [tool] = await executor(FakeChannel(tools=[spec])).listTools()
    assert tool.inputSchema == {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    }
    assert "caller" in spec.inputSchema["properties"]  # type: ignore[operator]


# Примечание: pydantic-схема D: caller через $ref — определение Caller тоже уходит, чужие остаются
def test_withoutCallerDropsUnusedDefs() -> None:
    spec = ToolSpec(
        name="list_items",
        description="",
        inputSchema={
            "type": "object",
            "$defs": {
                "Caller": {"type": "object", "properties": {"tz": {"$ref": "#/$defs/Tz"}}},
                "Tz": {"type": "string"},
                "Kind": {"enum": ["task", "event"]},
            },
            "properties": {
                "caller": {"$ref": "#/$defs/Caller"},
                "kinds": {"type": "array", "items": {"$ref": "#/$defs/Kind"}},
            },
            "required": ["caller"],
        },
    )
    schema = withoutCaller(spec).inputSchema
    assert schema["$defs"] == {"Kind": {"enum": ["task", "event"]}}
    assert schema["required"] == []
    assert "Caller" not in str(schema)


# Примечание: MCP недоступен при listTools — пустой список, Brain ответит без инструментов
async def test_listToolsUnavailable() -> None:
    # Примечание: канал, у которого listTools всегда падает
    class DownChannel(FakeChannel):
        # Примечание: нет связи
        @override
        async def listTools(self, timeoutS: float) -> list[ToolSpec]:
            self.listed += 1
            raise McpUnavailableError("нет связи")

    channel = DownChannel()
    assert await executor(channel).listTools() == []
    assert channel.listed == 2
