"""Настоящие инструменты через клиент MCP SDK и базу: так их вызывает агент C."""

from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from mcp import Client
from mcp_types import CallToolResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.core.db.models import ItemRow
from libs.core.services.time import now_utc
from services.mcp.server import build_server
from services.mcp.tools.context import ToolServices
from services.mcp.tools.stub import EXAMPLES_DIR, load_examples

pytestmark = pytest.mark.anyio

MOSCOW = ZoneInfo("Europe/Moscow")


def _caller(user_id: int) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "tz": "Europe/Moscow",
        "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
        "idempotency_key": "ab12f0c4d5e6f708192a3b4c5d6e7f80:1",
    }


async def _call(services: ToolServices, name: str, arguments: dict[str, Any]) -> CallToolResult:
    server = build_server(lambda: services, load_examples(EXAMPLES_DIR))
    async with Client(server) as client:
        return await client.call_tool(name, arguments)


async def test_remind_tomorrow_at_ten_creates_row_in_database(
    tool_services: ToolServices, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    # «Напомни завтра в 10 позвонить Ивану»
    tomorrow = (now_utc() + timedelta(days=1)).astimezone(MOSCOW).date()
    remind_at = f"{tomorrow.isoformat()}T10:00:00+03:00"

    result = await _call(
        tool_services,
        "create_task",
        {
            "caller": _caller(user_id),
            "title": "Позвонить Ивану",
            "kind": "reminder",
            "remind_at": remind_at,
            "source_text": "Напомни завтра в 10 позвонить Ивану",
        },
    )

    assert result.is_error is False
    item = result.structured_content["item"]
    assert result.structured_content["status"] == "created"
    assert item["remind_at"] == remind_at
    assert result.content[0].text.startswith(f"Создано: [{item['id'][:8]}] ")
    assert result.content[0].text.endswith(" 10:00 Позвонить Ивану")
    async with session_factory() as session:
        row = await session.scalar(select(ItemRow))
    assert row is not None
    assert (str(row.id), row.user_id, row.kind, row.title) == (
        item["id"],
        user_id,
        "reminder",
        "Позвонить Ивану",
    )


async def test_service_error_becomes_tool_error(tool_services: ToolServices, user_id: int) -> None:
    past = (now_utc() - timedelta(hours=1)).astimezone(MOSCOW).replace(microsecond=0)

    result = await _call(
        tool_services,
        "create_task",
        {
            "caller": _caller(user_id),
            "title": "Позвонить Ивану",
            "kind": "reminder",
            "remind_at": past.isoformat(),
        },
    )

    assert result.is_error is True
    assert result.structured_content == {
        "error": {
            "code": "past_time",
            "message": "Время уже прошло",
            "details": {"remind_at": past.isoformat()},
        }
    }
    assert result.content[0].text == "Ошибка past_time: время уже прошло"


async def test_reminder_without_time_asks_model_for_it(
    tool_services: ToolServices, user_id: int
) -> None:
    result = await _call(
        tool_services,
        "create_task",
        {"caller": _caller(user_id), "title": "Позвонить Ивану", "kind": "reminder"},
    )

    assert result.is_error is True
    assert result.structured_content["error"]["code"] == "validation_error"
    assert result.structured_content["error"]["details"] == {"missing": ["remind_at"]}


async def test_second_meeting_at_same_time_is_a_conflict(
    tool_services: ToolServices, user_id: int
) -> None:
    # «Поставь встречу с Иваном завтра в три», а в три уже встреча с дизайнером.
    tomorrow = (now_utc() + timedelta(days=1)).astimezone(MOSCOW).date()
    starts_at = f"{tomorrow.isoformat()}T15:00:00+03:00"
    caller = {**_caller(user_id), "idempotency_key": None}
    first = await _call(
        tool_services,
        "create_event",
        {"caller": caller, "title": "Встреча с дизайнером", "starts_at": starts_at},
    )

    second = await _call(
        tool_services,
        "create_event",
        {"caller": caller, "title": "Встреча с Иваном", "starts_at": starts_at},
    )

    assert first.structured_content["status"] == "created"
    assert first.content[0].text.endswith(" 15:00–16:00 Встреча с дизайнером")
    assert second.is_error is False
    assert second.structured_content["status"] == "conflict"
    assert second.structured_content["item"] is None
    assert second.structured_content["free_slots"] == [
        {
            "starts_at": f"{tomorrow.isoformat()}T16:00:00+03:00",
            "ends_at": f"{tomorrow.isoformat()}T17:00:00+03:00",
        }
    ]
    assert second.content[0].text == (
        "Не создано: пересекается с «Встреча с дизайнером» в 15:00. Свободно: 16:00–17:00."
    )


async def test_what_do_i_have_tomorrow_lists_by_day(
    tool_services: ToolServices, user_id: int
) -> None:
    tomorrow = (now_utc() + timedelta(days=1)).astimezone(MOSCOW).date()
    day_after = tomorrow + timedelta(days=1)
    caller = {**_caller(user_id), "idempotency_key": None}
    created = await _call(
        tool_services,
        "create_event",
        {
            "caller": caller,
            "title": "Встреча с Иваном",
            "starts_at": f"{tomorrow.isoformat()}T15:00:00+03:00",
        },
    )

    # «Что у меня завтра?»
    result = await _call(
        tool_services,
        "list_items",
        {"caller": caller, "from": tomorrow.isoformat(), "to": day_after.isoformat()},
    )

    assert result.is_error is False
    assert result.structured_content["range"] == {
        "from": tomorrow.isoformat(),
        "to": day_after.isoformat(),
    }
    assert [item["id"] for item in result.structured_content["items"]] == [
        created.structured_content["item"]["id"]
    ]
    short = created.structured_content["item"]["id"][:8]
    assert result.content[0].text.endswith(f"\n[{short}] 15:00–16:00 Встреча с Иваном")


async def test_find_what_i_wrote_about_ivan(tool_services: ToolServices, user_id: int) -> None:
    caller = {**_caller(user_id), "idempotency_key": None}
    created = await _call(
        tool_services, "create_task", {"caller": caller, "title": "Позвонить Ивану"}
    )

    result = await _call(tool_services, "search_items", {"caller": caller, "query": "Иван"})

    item_id = created.structured_content["item"]["id"]
    assert result.is_error is False
    assert [hit["item"]["id"] for hit in result.structured_content["results"]] == [item_id]
    assert result.structured_content["best_match_confident"] is True
    assert result.content[0].text == f"Найдено: [{item_id[:8]}] Позвонить Ивану"
