import pytest
from pydantic import ValidationError

from libs.contracts.items import Item, ItemBrief, ItemKind, ItemStatus

PLAN_ITEM_EXAMPLE = {
    "id": "7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90",
    "kind": "event",
    "title": "Встреча с Иваном",
    "notes": None,
    "starts_at": "2026-10-18T15:00:00+03:00",
    "ends_at": "2026-10-18T16:00:00+03:00",
    "all_day": False,
    "remind_at": "2026-10-18T14:45:00+03:00",
    "status": "open",
    "rrule": None,
    "participants": ["Иван"],
    "source_text": "Поставь встречу с Иваном завтра в три",
    "created_at": "2026-10-17T15:42:07+03:00",
    "updated_at": "2026-10-17T15:42:07+03:00",
    "occurrence_index": 0,
}


def test_plan_example_parses_and_round_trips() -> None:
    item = Item.model_validate(PLAN_ITEM_EXAMPLE)

    assert item.kind is ItemKind.EVENT
    assert item.status is ItemStatus.OPEN
    assert item.model_dump(mode="json") == PLAN_ITEM_EXAMPLE


def test_time_without_offset_is_rejected() -> None:
    payload = {**PLAN_ITEM_EXAMPLE, "starts_at": "2026-10-18T15:00:00"}

    with pytest.raises(ValidationError):
        Item.model_validate(payload)


def test_unknown_fields_are_ignored() -> None:
    item = Item.model_validate({**PLAN_ITEM_EXAMPLE, "color": "red"})

    assert "color" not in item.model_dump()


@pytest.mark.parametrize(
    ("field", "value"),
    [("kind", "meeting"), ("status", "deleted"), ("title", ""), ("title", "x" * 201)],
)
def test_invalid_values_are_rejected(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Item.model_validate({**PLAN_ITEM_EXAMPLE, field: value})


def test_brief_is_built_from_full_item() -> None:
    item = Item.model_validate(PLAN_ITEM_EXAMPLE)

    brief = ItemBrief.model_validate(item.model_dump())

    assert brief.model_dump(mode="json") == {
        "id": "7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90",
        "kind": "event",
        "title": "Встреча с Иваном",
        "starts_at": "2026-10-18T15:00:00+03:00",
        "all_day": False,
        "status": "open",
    }
