"""Инструмент create_event: встреча; при пересечении — конфликт и свободные окна."""

from mcp_types import CallToolResult, TextContent

from libs.contracts.tools import CreateEventArgs
from libs.core.services.items import ItemService
from services.mcp.tools.text import conflict_text, created_text


async def create_event(service: ItemService, args: CreateEventArgs) -> CallToolResult:
    result = await service.create_event(args)
    if result.status == "conflict":
        text = conflict_text(result.conflicts, result.free_slots)
    else:
        text = created_text(result.item)
    return CallToolResult(
        content=[TextContent(text=text)], structured_content=result.model_dump(mode="json")
    )
