"""MCP-сервер (J3): 8 инструментов со схемами из libs/contracts, работают через libs/core."""

import logging
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
from services.mcp.tools.check_conflicts import check_conflicts
from services.mcp.tools.complete_item import complete_item
from services.mcp.tools.context import ToolServices
from services.mcp.tools.create_event import create_event
from services.mcp.tools.create_task import create_task
from services.mcp.tools.delete_item import delete_item
from services.mcp.tools.list_items import list_items
from services.mcp.tools.search_items import search_items
from services.mcp.tools.text import error_text
from services.mcp.tools.update_item import update_item

logger = logging.getLogger(__name__)

ToolHandler = Callable[[ToolServices, Any], Awaitable[CallToolResult]]

TOOL_HANDLERS: dict[str, ToolHandler] = {
    "create_task": create_task,
    "create_event": create_event,
    "list_items": list_items,
    "search_items": search_items,
    "update_item": update_item,
    "complete_item": complete_item,
    "delete_item": delete_item,
    "check_conflicts": check_conflicts,
}


def build_server(services: Callable[[], ToolServices]) -> Server[Any]:
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
        handler = TOOL_HANDLERS[params.name]
        try:
            return await handler(services(), args)
        except DomainError as error:
            return _error_result(error.code, error.message, error.details)
        except Exception:
            # Ошибка в нашем коде или упала база: модели — internal без подробностей
            # (в них может быть SQL), в лог — traceback с trace_id агента.
            logger.exception("Инструмент %s упал, trace_id=%s", params.name, args.caller.trace_id)
            return _error_result(ErrorCode.INTERNAL, "Внутренняя ошибка", {})

    return Server("inote-mcp", on_list_tools=list_tools, on_call_tool=call_tool)


# Тексты для модели пишет B: когда вызывать инструмент и что делать с ответом. Модель выбирает
# инструмент по этим строкам, поэтому в них правила, а не пересказ схемы.
TOOL_DESCRIPTIONS: dict[str, str] = {
    "create_task": (
        "Создать задачу или напоминание. kind=reminder — напомнить в момент remind_at (время "
        "обязательно); kind=task — задача со сроком starts_at или без срока. Встречи с людьми — "
        "create_event."
    ),
    "create_event": (
        "Создать встречу, starts_at обязателен. ends_at передавай, только если названа "
        "длительность. Если время занято, ответ status=conflict со свободными окнами free_slots: "
        "встреча не создана, предложи пользователю окно."
    ),
    "list_items": (
        "Показать записи за период: «что у меня сегодня, завтра, на неделе». from и to — даты "
        "в поясе пользователя, to не включается: на завтра from=завтра, to=послезавтра."
    ),
    "search_items": (
        "Найти записи по смыслу: «звонок Ивану», «договор». Вызывай перед изменением, удалением "
        "или отметкой, если записи нет в фокусе: в ответе id для следующего вызова."
    ),
    "update_item": (
        "Изменить запись по id: переданные поля заменяются, null очищает поле, остальные не "
        "меняются. Перенос встречи или срока задачи — starts_at, напоминания — remind_at. "
        "Конфликт времени — как у create_event."
    ),
    "complete_item": "Отметить запись выполненной по id; done=false снимает отметку.",
    "delete_item": "Удалить запись по id; restore=true восстанавливает удалённую.",
    "check_conflicts": (
        "Проверить, свободно ли время для встречи, ничего не создавая: вернёт пересечения и "
        "свободные окна. exclude_id — запись, которую переносим."
    ),
}


def list_tool_specs() -> list[Tool]:
    # Схемы строятся из моделей libs/contracts, чтобы сервер и контракт не разошлись.
    return [
        Tool(
            name=name,
            description=TOOL_DESCRIPTIONS[name],
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
