"""Сервисы libs/core, которые получают инструменты MCP."""

from dataclasses import dataclass

from libs.core.services.items import ItemService
from libs.core.services.search import SearchService


@dataclass(frozen=True)
class ToolServices:
    items: ItemService
    search: SearchService
