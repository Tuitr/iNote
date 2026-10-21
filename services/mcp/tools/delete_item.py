"""Инструмент delete_item: мягко удалить запись или вернуть удалённую (restore)."""

from mcp_types import CallToolResult, TextContent

from libs.contracts.tools import DeleteItemArgs
from services.mcp.tools.context import ToolServices
from services.mcp.tools.text import item_line


async def delete_item(services: ToolServices, args: DeleteItemArgs) -> CallToolResult:
    result = await services.items.delete_item(args)
    prefix = "Восстановлено" if args.restore else "Удалено"
    return CallToolResult(
        content=[TextContent(text=item_line(prefix, result.item))],
        structured_content=result.model_dump(mode="json"),
    )
