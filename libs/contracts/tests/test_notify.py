import pytest
from pydantic import ValidationError

from common.errors import ErrorEnvelope
from contracts.notify import CHAT_NOT_FOUND, NotifyDelivered, NotifyRequest
from libs.contracts.tests.examples import loadExample


# Примечание: пример запроса Scheduler валиден и сериализуется обратно без потерь
def test_requestRoundTrip() -> None:
    raw = loadExample("notify/request.json")
    request = NotifyRequest.fromWire(raw)
    assert request.toWire() == raw
    assert request.user.tz == "Europe/Moscow"
    assert request.attempt == 1


# Примечание: оба вида 200-ответа
@pytest.mark.parametrize("name", ["notify/delivered.json", "notify/duplicate.json"])
def test_deliveredRoundTrip(name: str) -> None:
    raw = loadExample(name)
    assert NotifyDelivered.fromWire(raw).toWire() == raw


# Примечание: 404 — стандартный конверт с кодом chat_not_found
def test_chatNotFoundIsStandardEnvelope() -> None:
    envelope = ErrorEnvelope.fromWire(loadExample("notify/chat_not_found.json"))
    assert envelope.error.code == CHAT_NOT_FOUND


# Примечание: время без смещения, неизвестный пояс и номер попытки вне 1…5 — ошибка
@pytest.mark.parametrize(
    ("path", "value"),
    [("fire_at", "2026-10-18T10:00:00"), ("attempt", 6), ("attempt", 0), ("tz", "Mars/Base")],
)
def test_invalidRequest(path: str, value: object) -> None:
    raw = loadExample("notify/request.json")
    assert isinstance(raw, dict)
    if path == "tz":
        user = raw["user"]
        assert isinstance(user, dict)
        user["tz"] = value
    else:
        raw[path] = value
    with pytest.raises(ValidationError):
        NotifyRequest.fromWire(raw)


# Примечание: delivered=false не бывает — недоставка выражается ошибкой, а не флагом
def test_deliveredFalseRejected() -> None:
    with pytest.raises(ValidationError):
        NotifyDelivered.fromWire({"delivered": False})
