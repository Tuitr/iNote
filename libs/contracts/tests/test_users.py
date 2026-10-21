import pytest
from pydantic import ValidationError

from contracts.users import ResolveUserRequest, ResolveUserResponse
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
