"""MCP-заглушка (шаг 5 S0): 8 инструментов проверяют аргументы по контракту J3
и отвечают готовыми примерами из contracts/examples/j3.

Нужна C и B, чтобы подключить агента до настоящей реализации. В S1 инструменты
по одному заменяются на настоящие, а этот модуль удаляется.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp_types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
)
from pydantic import ValidationError

from libs.contracts.errors import ErrorCode, ErrorInfo, ToolErrorResponse
from libs.contracts.tools import TOOLS
from libs.core.validation import validation_error_details

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "contracts" / "examples" / "j3"


@dataclass(frozen=True)
class ToolExample:
    structured_content: dict[str, Any]
    text: str


def load_examples(examples_dir: Path) -> dict[str, ToolExample]:
    examples = {}
    for name in TOOLS:
        raw = json.loads((examples_dir / f"{name}.json").read_text(encoding="utf-8"))
        examples[name] = ToolExample(structured_content=raw["structuredContent"], text=raw["text"])
    return examples


def _list_tool_specs() -> list[Tool]:
    # Схемы строятся из моделей libs/contracts, чтобы заглушка и контракт не разошлись.
    # Описаний нет: их тексты пишет B.
    return [
        Tool(
            name=name,
            input_schema=contract.arguments.model_json_schema(),
            output_schema=contract.result.model_json_schema(),
        )
        for name, contract in TOOLS.items()
    ]


def _validation_error_result(error: ValidationError) -> CallToolResult:
    details = validation_error_details(error.errors())
    return _error_result(ErrorCode.VALIDATION_ERROR, "Неверные аргументы инструмента", details)


def _error_result(code: ErrorCode, message: str, details: dict[str, Any]) -> CallToolResult:
    body = ToolErrorResponse(error=ErrorInfo(code=code, message=message, details=details))
    return CallToolResult(
        content=[TextContent(text=f"Ошибка {code}: {message}")],
        structured_content=body.model_dump(mode="json"),
        is_error=True,
    )


def build_stub_server(examples: dict[str, ToolExample]) -> Server[Any]:
    tool_specs = _list_tool_specs()

    async def list_tools(
        ctx: ServerRequestContext[Any], params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        return ListToolsResult(tools=tool_specs)

    async def call_tool(
        ctx: ServerRequestContext[Any], params: CallToolRequestParams
    ) -> CallToolResult:
        contract = TOOLS.get(params.name)
        if contract is None:
            return _error_result(
                ErrorCode.NOT_FOUND, "Нет такого инструмента", {"name": params.name}
            )
        try:
            contract.arguments.model_validate(params.arguments or {})
        except ValidationError as error:
            return _validation_error_result(error)
        example = examples[params.name]
        return CallToolResult(
            content=[TextContent(text=example.text)],
            structured_content=example.structured_content,
        )

    return Server("inote-mcp-stub", on_list_tools=list_tools, on_call_tool=call_tool)
