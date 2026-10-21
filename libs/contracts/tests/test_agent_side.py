import pytest
from libs.contracts.tests.examples import loadExample
from pydantic import ValidationError

from contracts.agent_side import ToolResult, TurnInput, TurnResult


# Примечание: примеры J2 валидны и сериализуются обратно без потерь
@pytest.mark.parametrize(
    ("name", "model"),
    [
        ("brain/turn_input.json", TurnInput),
        ("brain/tool_result_ok.json", ToolResult),
        ("brain/tool_result_error.json", ToolResult),
        ("brain/turn_result_done.json", TurnResult),
        ("brain/turn_result_clarify.json", TurnResult),
        ("brain/turn_result_error.json", TurnResult),
    ],
)
def test_examplesRoundTrip(name: str, model: type[TurnInput | ToolResult | TurnResult]) -> None:
    raw = loadExample(name)
    assert model.fromWire(raw).toWire() == raw


# Примечание: TurnInput не содержит user_id — Brain его не видит (правило 1)
def test_turnInputHasNoUserId() -> None:
    fields = set(TurnInput.model_json_schema(by_alias=True)["properties"])
    assert not any("user_id" in f for f in fields)
    raw = loadExample("brain/turn_input.json")
    assert isinstance(raw, dict)
    assert "user_id" not in TurnInput.fromWire({**raw, "user_id": 17}).toWire()


# Примечание: now должен быть в поясе пользователя (и в DST-поясе тоже)
@pytest.mark.parametrize(
    ("now", "tz", "valid"),
    [
        ("2026-10-17T21:30:00+03:00", "Europe/Moscow", True),
        ("2026-10-17T18:30:00+00:00", "Europe/Moscow", False),
        ("2026-10-17T23:30:00+05:00", "Asia/Yekaterinburg", True),
        ("2026-07-01T12:00:00+02:00", "Europe/Berlin", True),
        ("2026-12-01T12:00:00+02:00", "Europe/Berlin", False),
        ("2026-10-17T21:30:00", "Europe/Moscow", False),
    ],
)
def test_nowMatchesTz(now: str, tz: str, valid: bool) -> None:
    raw = loadExample("brain/turn_input.json")
    assert isinstance(raw, dict)
    data = {**raw, "now": now, "tz": tz}
    if valid:
        TurnInput.fromWire(data)
    else:
        with pytest.raises(ValidationError):
            TurnInput.fromWire(data)


# Примечание: история не длиннее 10 реплик
def test_historyLimit() -> None:
    raw = loadExample("brain/turn_input.json")
    assert isinstance(raw, dict)
    history = raw["history"]
    assert isinstance(history, list)
    with pytest.raises(ValidationError, match="10"):
        TurnInput.fromWire({**raw, "history": history * 6})


# Примечание: ok и error согласованы
def test_toolResultConsistency() -> None:
    with pytest.raises(ValidationError, match="ok"):
        ToolResult(ok=True, summary="x", error={"code": "internal", "message": "x"})  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="ok"):
        ToolResult(ok=False, summary="x")


# Примечание: clarify ⇔ outcome=clarify, error_code ⇔ outcome=error
@pytest.mark.parametrize(
    "patch",
    [{"outcome": "done"}, {"outcome": "clarify", "clarify": None}, {"error_code": "llm_timeout"}],
)
def test_turnResultConsistency(patch: dict[str, object]) -> None:
    raw = loadExample("brain/turn_result_clarify.json")
    assert isinstance(raw, dict)
    with pytest.raises(ValidationError, match="TurnResult"):
        TurnResult.fromWire({**raw, **patch})
