import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from libs.contracts.internal import (
    DialogTurnsRequest,
    ResolveUserRequest,
    ResolveUserResponse,
)

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "contracts" / "examples" / "j8"

MODEL_BY_EXAMPLE: dict[str, type[BaseModel]] = {
    "users_resolve.request": ResolveUserRequest,
    "users_resolve.response": ResolveUserResponse,
    "dialog_turns.request": DialogTurnsRequest,
}


def test_every_example_file_has_a_model() -> None:
    assert {path.stem for path in EXAMPLES_DIR.glob("*.json")} == set(MODEL_BY_EXAMPLE)


@pytest.mark.parametrize("name", sorted(MODEL_BY_EXAMPLE))
def test_example_matches_contract_exactly(name: str) -> None:
    example = json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))

    parsed = MODEL_BY_EXAMPLE[name].model_validate(example)

    assert parsed.model_dump(mode="json", exclude_unset=True) == example


def test_resolve_needs_only_max_user_id() -> None:
    request = ResolveUserRequest.model_validate({"max_user_id": 123456789})

    assert (request.max_chat_id, request.name) == (None, None)


def test_resolve_response_rejects_unknown_tz() -> None:
    settings = {"tz": "Europe/Moscow"}

    with pytest.raises(ValidationError):
        ResolveUserResponse.model_validate(
            {"user_id": 17, "tz": "Mars/Olympus", "settings": settings, "created": True}
        )


@pytest.mark.parametrize(
    "changes",
    [{"turns": []}, {"trace_id": "not-a-trace"}, {"latency_ms": -1}],
)
def test_dialog_turns_rejects_bad_fields(changes: dict) -> None:
    body = {
        "user_id": 17,
        "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
        "turns": [{"role": "user", "text": "Привет"}],
    }

    with pytest.raises(ValidationError):
        DialogTurnsRequest.model_validate({**body, **changes})
