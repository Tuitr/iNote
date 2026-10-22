"""Инструмент complete_item: отметить выполненным или вернуть в работу."""

from mcp_types import CallToolResult, TextContent

from libs.contracts.tools import CompleteItemArgs
from services.mcp.tools.context import ToolServices
from services.mcp.tools.text import item_line


async def complete_item(services: ToolServices, args: CompleteItemArgs) -> CallToolResult:
    result = await services.items.complete_item(args)
    prefix = "Выполнено" if args.done else "Снова в работе"
    if args.done and result.item is not None and result.item.status == "open":
        # Вхождение серии выполнено, запись уже на следующей дате.
        prefix = "Выполнено, следующий раз"
    return CallToolResult(
        content=[TextContent(text=item_line(prefix, result.item))],
        structured_content=result.model_dump(mode="json"),
    )
