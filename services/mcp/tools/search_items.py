"""Инструмент search_items: поиск старых записей по смыслу."""

from mcp_types import CallToolResult, TextContent

from libs.contracts.tools import SearchItemsArgs
from services.mcp.tools.context import ToolServices
from services.mcp.tools.text import search_text


async def search_items(services: ToolServices, args: SearchItemsArgs) -> CallToolResult:
    result = await services.search.search(args)
    return CallToolResult(
        content=[TextContent(text=search_text([hit.item for hit in result.results]))],
        structured_content=result.model_dump(mode="json"),
    )
