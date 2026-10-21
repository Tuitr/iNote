"""Инструмент complete_item: отметить выполненным или вернуть в работу."""

from mcp_types import CallToolResult, TextContent

from libs.contracts.tools import CompleteItemArgs
from services.mcp.tools.context import ToolServices
from services.mcp.tools.text import item_line


async def complete_item(services: ToolServices, args: CompleteItemArgs) -> CallToolResult:
    result = await services.items.complete_item(args)
    prefix = "Выполнено" if args.done else "Снова в работе"
    return CallToolResult(
        content=[TextContent(text=item_line(prefix, result.item))],
        structured_content=result.model_dump(mode="json"),
    )
