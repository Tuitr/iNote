import pytest
from pydantic import ValidationError

from contracts.focus import FocusRequest
from libs.contracts.tests.examples import loadExample


# Примечание: пример бота валиден и сериализуется обратно без потерь; «когда» — remind_at
def test_requestRoundTrip() -> None:
    raw = loadExample("agent/focus_request.json")
    request = FocusRequest.fromWire(raw)
    assert request.toWire() == raw
    assert request.item.moment is not None
    assert request.item.moment.isoformat() == "2026-10-22T07:00:00+00:00"


# Примечание: без записи или с неизвестным видом записи — ошибка контракта
@pytest.mark.parametrize("item", [None, {"id": "7f3c9a12-4b5e-4d6f-8a9b-0c1d2e3f4a5b"}])
def test_invalidRequest(item: object) -> None:
    raw = loadExample("agent/focus_request.json")
    assert isinstance(raw, dict)
    with pytest.raises(ValidationError):
        FocusRequest.fromWire({**raw, "item": item})
