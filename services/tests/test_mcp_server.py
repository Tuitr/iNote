"""MCP-сервер без базы: схемы, проверка аргументов, примеры для ещё не сделанных инструментов."""

import asyncio
import json
from typing import Any

import pytest
from mcp import Client
from mcp_types import CallToolResult

from libs.contracts.tools import TOOLS
from services.mcp.export_tools import TOOLS_PATH, render_tools
from services.mcp.server import REAL_TOOLS, build_server
from services.mcp.tools.context import ToolServices
from services.mcp.tools.stub import EXAMPLES_DIR, load_examples

CALLER = {"user_id": 17, "tz": "Europe/Moscow", "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80"}
STUB_TOOLS = sorted(set(TOOLS) - set(REAL_TOOLS))


def _no_service() -> ToolServices:
    pytest.fail("Этот вызов не должен доходить до базы")


def _server() -> Any:
    return build_server(_no_service, load_examples(EXAMPLES_DIR))


def _call(name: str, arguments: dict[str, Any]) -> CallToolResult:
    async def scenario() -> CallToolResult:
        async with Client(_server()) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(scenario())


def test_lists_all_eight_tools_with_schemas_from_contracts() -> None:
    async def scenario() -> list[Any]:
        async with Client(_server()) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(scenario())

    assert [tool.name for tool in tools] == list(TOOLS)
    for tool in tools:
        assert tool.input_schema == TOOLS[tool.name].arguments.model_json_schema()
        assert tool.output_schema == TOOLS[tool.name].result.model_json_schema()


@pytest.mark.parametrize("name", STUB_TOOLS)
def test_not_yet_real_tool_returns_example(name: str) -> None:
    example = json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))

    result = _call(name, example["arguments"])

    assert result.is_error is False
    assert result.structured_content == example["structuredContent"]
    assert result.content[0].text == example["text"]


def test_missing_and_invalid_fields_are_reported_without_values() -> None:
    result = _call(
        "create_event", {"caller": CALLER, "title": "Встреча", "ends_at": "2026-10-18T15:00:00"}
    )

    assert result.is_error is True
    assert result.structured_content == {
        "error": {
            "code": "validation_error",
            "message": "Неверные аргументы инструмента",
            "details": {"missing": ["starts_at"], "invalid": ["ends_at"]},
        }
    }
    assert result.content[0].text == "Ошибка validation_error: неверные аргументы инструмента"


def test_call_without_caller_is_rejected_before_the_service() -> None:
    result = _call("create_task", {"title": "Позвонить Ивану"})

    assert result.is_error is True
    assert result.structured_content["error"]["details"]["missing"] == ["caller"]


def test_unknown_tool_is_reported_as_error() -> None:
    result = _call("drop_database", {})

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "not_found"


def test_committed_tool_schemas_are_up_to_date() -> None:
    # Поменяли аргументы или ответ инструмента и не обновили снапшот — тест красный (КиП).
    # Обновить, если изменение согласовано с C и B: python -m services.mcp.export_tools
    assert TOOLS_PATH.read_text(encoding="utf-8") == render_tools()
