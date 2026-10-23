import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from libs.contracts.errors import ToolErrorResponse
from libs.contracts.tools import TOOLS, CreateEventArgs, ListItemsArgs, UpdateItemArgs

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "contracts" / "examples" / "j3"
CALLER = {"user_id": 17, "tz": "Europe/Moscow", "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80"}


def _load_example(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def test_there_are_exactly_eight_tools_each_with_an_example() -> None:
    example_names = {path.stem for path in EXAMPLES_DIR.glob("*.json")} - {"error_past_time"}

    assert len(TOOLS) == 8
    assert set(TOOLS) == example_names


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_example_matches_contract_exactly(name: str) -> None:
    example = _load_example(name)
    contract = TOOLS[name]

    arguments = contract.arguments.model_validate(example["arguments"])
    result = contract.result.model_validate(example["structuredContent"])

    assert arguments.model_dump(mode="json", exclude_unset=True) == example["arguments"]
    assert result.model_dump(mode="json") == example["structuredContent"]
    assert example["text"]


def test_error_example_matches_contract() -> None:
    example = _load_example("error_past_time")

    assert example["isError"] is True
    ToolErrorResponse.model_validate(example["structuredContent"])


@pytest.mark.parametrize(
    "starts_at", ["2026-10-18T15:00:00", "2026-10-18T00:00:00", "18.10.2026", "завтра"]
)
def test_time_without_offset_or_free_text_is_rejected(starts_at: str) -> None:
    with pytest.raises(ValidationError):
        CreateEventArgs.model_validate(
            {"caller": CALLER, "title": "Встреча", "starts_at": starts_at}
        )


def test_date_without_time_means_all_day_and_is_accepted() -> None:
    args = CreateEventArgs.model_validate(
        {"caller": CALLER, "title": "Конференция", "starts_at": "2026-10-18"}
    )

    assert args.starts_at.isoformat() == "2026-10-18"


@pytest.mark.parametrize("item_id", ["7f3a9c2e", "7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90"])
def test_full_or_short_id_is_accepted(item_id: str) -> None:
    UpdateItemArgs.model_validate({"caller": CALLER, "id": item_id})


@pytest.mark.parametrize("item_id", ["7f3a", "7F3A9C2E", "7f3a9c2e-5b1d", "../7f3a9c2e"])
def test_malformed_id_is_rejected(item_id: str) -> None:
    with pytest.raises(ValidationError):
        UpdateItemArgs.model_validate({"caller": CALLER, "id": item_id})


def test_update_distinguishes_null_from_not_passed() -> None:
    args = UpdateItemArgs.model_validate({"caller": CALLER, "id": "7f3a9c2e", "remind_at": None})

    assert "remind_at" in args.model_fields_set
    assert "notes" not in args.model_fields_set


def test_list_items_uses_from_in_json() -> None:
    args = ListItemsArgs.model_validate(
        {"caller": CALLER, "from": "2026-10-18", "to": "2026-10-19"}
    )

    assert args.model_dump(mode="json")["from"] == "2026-10-18"
    assert "from" in ListItemsArgs.model_json_schema()["properties"]
