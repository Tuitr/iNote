import asyncio
import json
from typing import Any

import pytest
from mcp import Client
from mcp_types import CallToolResult

from libs.contracts.tools import TOOLS
from services.mcp.tools.stub import EXAMPLES_DIR, build_stub_server, load_examples

CALLER = {"user_id": 17, "tz": "Europe/Moscow", "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80"}


def _call(name: str, arguments: dict[str, Any]) -> CallToolResult:
    async def scenario() -> CallToolResult:
        async with Client(build_stub_server(load_examples(EXAMPLES_DIR))) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(scenario())


def test_lists_all_eight_tools_with_schemas_from_contracts() -> None:
    async def scenario() -> list[Any]:
        async with Client(build_stub_server(load_examples(EXAMPLES_DIR))) as client:
            return (await client.list_tools()).tools

    tools = asyncio.run(scenario())

    assert [tool.name for tool in tools] == list(TOOLS)
    for tool in tools:
        assert tool.input_schema == TOOLS[tool.name].arguments.model_json_schema()
        assert tool.output_schema == TOOLS[tool.name].result.model_json_schema()


@pytest.mark.parametrize("name", list(TOOLS))
def test_valid_call_returns_example(name: str) -> None:
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
    assert result.content[0].text.startswith("Ошибка validation_error")


def test_call_without_caller_is_rejected() -> None:
    result = _call("list_items", {"from": "2026-10-18", "to": "2026-10-19"})

    assert result.is_error is True
    assert result.structured_content["error"]["details"]["missing"] == ["caller"]


def test_unknown_tool_is_reported_as_error() -> None:
    result = _call("drop_database", {})

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "not_found"
