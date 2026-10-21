"""Инструмент create_task: задача или напоминание."""

from mcp_types import CallToolResult, TextContent

from libs.contracts.tools import CreateTaskArgs
from libs.core.services.items import ItemService
from services.mcp.tools.text import created_text


async def create_task(service: ItemService, args: CreateTaskArgs) -> CallToolResult:
    result = await service.create_task(args)
    # У задач нет конфликтов, поэтому create_task всегда возвращает запись.
    return CallToolResult(
        content=[TextContent(text=created_text(result.item))],
        structured_content=result.model_dump(mode="json"),
    )
