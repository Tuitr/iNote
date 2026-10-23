import pytest
from pydantic import ValidationError

from contracts.dialog_turns import DialogTurnsRequest
from libs.contracts.tests.examples import loadExample

EXAMPLE = "dialog/dialog_turns_request.json"


# Примечание: пример J8 dialog-turns валиден и сериализуется обратно без потерь
def test_roundTrip() -> None:
    raw = loadExample(EXAMPLE)
    assert DialogTurnsRequest.fromWire(raw).toWire() == raw


# Примечание: ровно две реплики — пользователь, затем ассистент; user_id внутренний (> 0)
@pytest.mark.parametrize(
    "patch",
    [
        {"user_id": 0},
        {"trace_id": ""},
        {"latency_ms": -1},
        {"turns": []},
        {
            "turns": [
                {"role": "assistant", "text": "x"},
                {"role": "user", "text": "y", "input_type": "text"},
            ]
        },
        {
            "turns": [
                {"role": "user", "text": "x", "input_type": "telepathy"},
                {"role": "assistant", "text": "y"},
            ]
        },
    ],
)
def test_invalid(patch: dict[str, object]) -> None:
    raw = loadExample(EXAMPLE)
    assert isinstance(raw, dict)
    with pytest.raises(ValidationError):
        DialogTurnsRequest.fromWire({**raw, **patch})
