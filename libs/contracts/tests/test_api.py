import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from libs.contracts.api import (
    AuthMaxRequest,
    AuthMaxResponse,
    DialogPage,
    ItemsByDayResponse,
    ItemsResponse,
    Me,
    MePatch,
)
from libs.contracts.items import Item
from libs.contracts.tools import SearchItemsResult

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "contracts" / "examples" / "j5"

MODEL_BY_EXAMPLE: dict[str, type[BaseModel]] = {
    "auth_max.request": AuthMaxRequest,
    "auth_max.response": AuthMaxResponse,
    "me.response": Me,
    "me_patch.request": MePatch,
    "me_patch.response": Me,
    "items_by_day.response": ItemsByDayResponse,
    "items.response": ItemsResponse,
    "items_search.response": SearchItemsResult,
    "item.response": Item,
    "dialog.response": DialogPage,
}


def test_every_example_file_has_a_model() -> None:
    assert {path.stem for path in EXAMPLES_DIR.glob("*.json")} == set(MODEL_BY_EXAMPLE)


@pytest.mark.parametrize("name", sorted(MODEL_BY_EXAMPLE))
def test_example_matches_contract_exactly(name: str) -> None:
    example = json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))

    parsed = MODEL_BY_EXAMPLE[name].model_validate(example)

    assert parsed.model_dump(mode="json", exclude_unset=True) == example
