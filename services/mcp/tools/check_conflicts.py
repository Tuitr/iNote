"""Инструмент check_conflicts: свободно ли время и какие окна есть рядом."""

from mcp_types import CallToolResult, TextContent

from libs.contracts.tools import CheckConflictsArgs
from services.mcp.tools.context import ToolServices
from services.mcp.tools.text import conflict_text


async def check_conflicts(services: ToolServices, args: CheckConflictsArgs) -> CallToolResult:
    result = await services.items.check_conflicts(args)
    if result.conflicts:
        text = conflict_text("Занято", result.conflicts, result.free_slots)
    else:
        text = "Время свободно"
    return CallToolResult(
        content=[TextContent(text=text)], structured_content=result.model_dump(mode="json")
    )
