import json
from datetime import UTC, datetime
from typing import Final

import pytest
from pydantic import ValidationError

from common.wire import WireModel


# Примечание: модель-образец с составными именами полей
class Sample(WireModel):
    traceId: str
    maxUserId: int
    startsAt: datetime
    durationMin: int | None = None


SAMPLE_JSON: Final = {
    "trace_id": "ab12",
    "max_user_id": 42,
    "starts_at": "2026-10-18T07:00:00Z",
    "duration_min": 60,
}


# Примечание: модель → JSON даёт snake_case
def test_modelToWireIsSnakeCase() -> None:
    model = Sample(
        traceId="ab12", maxUserId=42, startsAt=datetime(2026, 10, 18, 7, tzinfo=UTC), durationMin=60
    )
    assert model.toWire() == SAMPLE_JSON
    assert json.loads(model.toJson()) == SAMPLE_JSON
    # model_dump без аргументов тоже snake_case (serialize_by_alias)
    assert set(model.model_dump()) == set(SAMPLE_JSON)


# Примечание: JSON → модель и обратно без потерь
def test_wireRoundTrip() -> None:
    model = Sample.fromWire(SAMPLE_JSON)
    assert model.maxUserId == 42
    assert Sample.fromJson(model.toJson()) == model


# Примечание: читатель игнорирует незнакомые поля (правило 8)
def test_unknownFieldsIgnored() -> None:
    model = Sample.fromWire({**SAMPLE_JSON, "new_field": True})
    assert "new_field" not in model.toWire()


# Примечание: camelCase на проводе не принимаем молча за snake_case — только имя поля в коде
def test_requiredFieldMissing() -> None:
    with pytest.raises(ValidationError):
        Sample.fromWire({"trace_id": "x"})


# Примечание: модель неизменяема — значение «с провода» не правят на месте
def test_frozen() -> None:
    model = Sample.fromWire(SAMPLE_JSON)
    with pytest.raises(ValidationError):
        model.traceId = "other"  # type: ignore[misc]
