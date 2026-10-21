"""MCP-сервер (J3): 8 инструментов со схемами из libs/contracts.

Настоящие инструменты работают с базой через сервисы libs/core. Остальные до S2 отвечают
примерами из contracts/examples/j3, чтобы агент C и eval B могли ими пользоваться.
"""

from collections.abc import Awaitable, Callable
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
from libs.core.errors import DomainError
from libs.core.validation import validation_error_details
from services.mcp.tools.context import ToolServices
from services.mcp.tools.create_event import create_event
from services.mcp.tools.create_task import create_task
from services.mcp.tools.list_items import list_items
from services.mcp.tools.search_items import search_items
from services.mcp.tools.stub import ToolExample
from services.mcp.tools.text import error_text

ToolHandler = Callable[[ToolServices, Any], Awaitable[CallToolResult]]

REAL_TOOLS: dict[str, ToolHandler] = {
    "create_task": create_task,
    "create_event": create_event,
    "list_items": list_items,
    "search_items": search_items,
}


def build_server(
    services: Callable[[], ToolServices], examples: dict[str, ToolExample]
) -> Server[Any]:
    """services — функция: сервисы создаются при старте приложения, то есть позже сервера."""
    tool_specs = list_tool_specs()

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
            args = contract.arguments.model_validate(params.arguments or {})
        except ValidationError as error:
            details = validation_error_details(error.errors())
            return _error_result(
                ErrorCode.VALIDATION_ERROR, "Неверные аргументы инструмента", details
            )
        handler = REAL_TOOLS.get(params.name)
        if handler is None:
            example = examples[params.name]
            return CallToolResult(
                content=[TextContent(text=example.text)],
                structured_content=example.structured_content,
            )
        try:
            return await handler(services(), args)
        except DomainError as error:
            return _error_result(error.code, error.message, error.details)

    return Server("inote-mcp", on_list_tools=list_tools, on_call_tool=call_tool)


def list_tool_specs() -> list[Tool]:
    # Схемы строятся из моделей libs/contracts, чтобы сервер и контракт не разошлись.
    # Описаний нет: их тексты пишет B.
    return [
        Tool(
            name=name,
            input_schema=contract.arguments.model_json_schema(),
            output_schema=contract.result.model_json_schema(),
        )
        for name, contract in TOOLS.items()
    ]


def _error_result(code: ErrorCode, message: str, details: dict[str, Any]) -> CallToolResult:
    body = ToolErrorResponse(error=ErrorInfo(code=code, message=message, details=details))
    return CallToolResult(
        content=[TextContent(text=error_text(code, message))],
        structured_content=body.model_dump(mode="json"),
        is_error=True,
    )
