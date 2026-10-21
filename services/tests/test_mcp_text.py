import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest

from libs.contracts.items import Item
from libs.contracts.tools import ListItemsResult, MutationResult, SearchItemsResult
from services.mcp.tools.stub import EXAMPLES_DIR
from services.mcp.tools.text import (
    conflict_text,
    created_text,
    day_label,
    error_text,
    list_text,
    search_text,
)

MOSCOW = timezone(timedelta(hours=3))
ITEM = Item(
    id=UUID("3c9e1a7b-2d4f-4b6a-8c1e-5f7a9b0d2e4c"),
    kind="task",
    title="Купить молоко",
    notes=None,
    starts_at=None,
    ends_at=None,
    all_day=False,
    remind_at=None,
    status="open",
    rrule=None,
    participants=[],
    source_text=None,
    created_at=datetime(2026, 10, 20, 14, 5, tzinfo=MOSCOW),
    updated_at=datetime(2026, 10, 20, 14, 5, tzinfo=MOSCOW),
)


def _example(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def test_create_task_example_text_is_what_the_tool_writes() -> None:
    example = _example("create_task")
    item = Item.model_validate(example["structuredContent"]["item"])

    assert created_text(item) == example["text"]


def test_error_example_text_is_what_the_tool_writes() -> None:
    error = _example("error_past_time")["structuredContent"]["error"]

    assert error_text(error["code"], error["message"]) == _example("error_past_time")["text"]


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, "Создано: [3c9e1a7b] Купить молоко"),
        (
            {"starts_at": datetime(2026, 10, 22, tzinfo=MOSCOW), "all_day": True},
            "Создано: [3c9e1a7b] чт, 22 октября весь день Купить молоко",
        ),
        (
            {
                "starts_at": datetime(2026, 10, 22, 15, tzinfo=MOSCOW),
                "ends_at": datetime(2026, 10, 22, 16, tzinfo=MOSCOW),
            },
            "Создано: [3c9e1a7b] чт, 22 октября 15:00–16:00 Купить молоко",
        ),
    ],
)
def test_created_text_shows_when(changes: dict, expected: str) -> None:
    assert created_text(ITEM.model_copy(update=changes)) == expected


@pytest.mark.parametrize(
    ("day", "label"),
    [
        (datetime(2026, 10, 19), "пн, 19 октября"),
        (datetime(2026, 10, 25), "вс, 25 октября"),
        (datetime(2027, 1, 1), "пт, 1 января"),
    ],
)
def test_day_label(day: datetime, label: str) -> None:
    assert day_label(day.date()) == label


def test_create_event_conflict_example_text_is_what_the_tool_writes() -> None:
    example = _example("create_event")
    result = MutationResult.model_validate(example["structuredContent"])

    assert conflict_text(result.conflicts, result.free_slots) == example["text"]


def test_conflict_without_free_slots_says_so() -> None:
    result = MutationResult.model_validate(_example("create_event")["structuredContent"])

    assert conflict_text(result.conflicts, []) == (
        "Не создано: пересекается с «Встреча с дизайнером» в 15:00. Свободных окон в этот день нет."
    )


def test_list_items_example_text_is_what_the_tool_writes() -> None:
    example = _example("list_items")
    result = ListItemsResult.model_validate(example["structuredContent"])

    assert list_text(result.items, result.truncated) == example["text"]


def test_list_text_groups_by_day_and_marks_truncation() -> None:
    reminder = ITEM.model_copy(
        update={"kind": "reminder", "remind_at": datetime(2026, 10, 22, 10, tzinfo=MOSCOW)}
    )
    all_day = ITEM.model_copy(
        update={"starts_at": datetime(2026, 10, 23, tzinfo=MOSCOW), "all_day": True}
    )

    assert list_text([reminder, all_day], truncated=True) == (
        "чт, 22 октября\n"
        "[3c9e1a7b] 10:00 Купить молоко\n"
        "пт, 23 октября\n"
        "[3c9e1a7b] весь день Купить молоко\n"
        "Показаны первые 2, есть ещё: сузь период."
    )


def test_empty_list_text() -> None:
    assert list_text([], truncated=False) == "Записей нет"


def test_search_items_example_text_is_what_the_tool_writes() -> None:
    example = _example("search_items")
    result = SearchItemsResult.model_validate(example["structuredContent"])

    assert search_text([hit.item for hit in result.results]) == example["text"]


def test_search_text_lists_several_and_says_when_nothing_found() -> None:
    assert search_text([]) == "Ничего не найдено"
    assert search_text([ITEM, ITEM]) == (
        "Найдено:\n[3c9e1a7b] Купить молоко\n[3c9e1a7b] Купить молоко"
    )
