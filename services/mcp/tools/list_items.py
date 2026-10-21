"""Инструмент list_items: записи за период, сгруппированные по дням."""

from mcp_types import CallToolResult, TextContent

from libs.contracts.tools import ListItemsArgs
from libs.core.services.items import ItemService
from services.mcp.tools.text import list_text


async def list_items(service: ItemService, args: ListItemsArgs) -> CallToolResult:
    result = await service.list_items(args)
    return CallToolResult(
        content=[TextContent(text=list_text(result.items, result.truncated))],
        structured_content=result.model_dump(mode="json"),
    )
