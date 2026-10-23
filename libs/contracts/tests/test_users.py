import pytest
from pydantic import ValidationError

from contracts.users import (
    ResolveUserRequest,
    ResolveUserResponse,
    UpdateUserSettingsRequest,
    settingsPath,
)
from libs.contracts.tests.examples import loadExample


# Примечание: примеры J8 валидны и сериализуются обратно без потерь
@pytest.mark.parametrize(
    ("model", "name"),
    [
        (ResolveUserRequest, "users/resolve_request.json"),
        (ResolveUserResponse, "users/resolve_response.json"),
    ],
)
def test_roundTrip(model: type[ResolveUserRequest | ResolveUserResponse], name: str) -> None:
    raw = loadExample(name)
    assert model.fromWire(raw).toWire() == raw


# Примечание: читатель игнорирует незнакомые поля, а без settings берёт значения по умолчанию
def test_responseTolerant() -> None:
    response = ResolveUserResponse.fromWire(
        {"user_id": 5, "tz": "Asia/Yekaterinburg", "locale": "ru", "settings": {"theme": "x"}}
    )
    assert (response.userId, response.created) == (5, False)
    assert (response.settings.defaultEventMin, response.settings.voiceReplies) == (60, False)


# Примечание: неизвестный пояс и неположительный user_id — ошибка разбора, а не тихий дефолт
@pytest.mark.parametrize("patch", [{"tz": "Mars/Base"}, {"user_id": 0}, {"tz": ""}])
def test_invalidResponse(patch: dict[str, object]) -> None:
    raw = loadExample("users/resolve_response.json")
    assert isinstance(raw, dict)
    with pytest.raises(ValidationError):
        ResolveUserResponse.fromWire({**raw, **patch})


# Примечание: S3 — PATCH настроек: пример запроса и ответа; на проводе только заданные поля
def test_settingsRoundTrip() -> None:
    raw = loadExample("users/settings_request.json")
    request = UpdateUserSettingsRequest.fromWire(raw)
    assert request.toPatch() == raw
    response = loadExample("users/settings_response.json")
    assert ResolveUserResponse.fromWire(response).toWire() == response
    assert settingsPath(17) == "/internal/v1/users/17/settings"


# Примечание: пустой PATCH, пояс null и несуществующий пояс — ошибка контракта
@pytest.mark.parametrize("raw", [{}, {"tz": None}, {"tz": "Mars/Base"}])
def test_settingsInvalid(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        UpdateUserSettingsRequest.fromWire(raw)
