"""Инструмент update_item: изменить или перенести запись («перенеси звонок Ивану на 12»)."""

from mcp_types import CallToolResult, TextContent

from libs.contracts.tools import UpdateItemArgs
from services.mcp.tools.context import ToolServices
from services.mcp.tools.text import conflict_text, item_line


async def update_item(services: ToolServices, args: UpdateItemArgs) -> CallToolResult:
    result = await services.items.update_item(args)
    if result.status == "conflict":
        text = conflict_text("Не перенесено", result.conflicts, result.free_slots)
    else:
        text = item_line("Изменено", result.item)
    return CallToolResult(
        content=[TextContent(text=text)], structured_content=result.model_dump(mode="json")
    )
