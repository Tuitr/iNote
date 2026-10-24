"""Зеркало C моделей J8 сверяется с примерами владельца D (contracts/examples/j8).

Поля зеркала — подмножество ответа D: читатель игнорирует незнакомые поля, а то, что зеркало
отправляет, должно совпадать с тем, что ждёт D.
"""

import pytest
from pydantic import ValidationError

from contracts.users import (
    ResolveUserRequest,
    ResolveUserResponse,
    UpdateUserSettingsRequest,
    UserSettingsResponse,
    settingsPath,
)
from libs.contracts.tests.examples import loadExample

RESOLVE_REQUEST = "j8/users_resolve.request.json"
RESOLVE_RESPONSE = "j8/users_resolve.response.json"
SETTINGS_REQUEST = "j8/users_settings.request.json"
SETTINGS_RESPONSE = "j8/users_settings.response.json"


def _example(name: str) -> dict[str, object]:
    raw = loadExample(name)
    assert isinstance(raw, dict)
    return raw


# Примечание: запрос resolve у C — те же поля и значения, что в примере D
def test_resolveRequestMatchesD() -> None:
    raw = _example(RESOLVE_REQUEST)
    assert ResolveUserRequest.fromWire(raw).toWire() == raw


# Примечание: ответ resolve D разбирается зеркалом: user_id, пояс и длительность встречи
def test_resolveResponseFromD() -> None:
    response = ResolveUserResponse.fromWire(_example(RESOLVE_RESPONSE))
    assert (response.userId, response.tz, response.created) == (17, "Europe/Moscow", False)
    assert (response.settings.defaultEventMin, response.settings.voiceReplies) == (60, False)


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
    with pytest.raises(ValidationError):
        ResolveUserResponse.fromWire({**_example(RESOLVE_RESPONSE), **patch})


# Примечание: PATCH настроек — агент шлёт только поля из запроса D, ответ D — настройки без user_id
def test_settingsMatchD() -> None:
    sent = UpdateUserSettingsRequest(tz="Asia/Yekaterinburg").toPatch()
    expected = _example(SETTINGS_REQUEST)
    assert sent.items() <= expected.items()
    saved = UserSettingsResponse.fromWire(_example(SETTINGS_RESPONSE))
    assert (saved.tz, saved.defaultEventMin) == ("Asia/Yekaterinburg", 60)
    assert settingsPath(17) == "/internal/v1/users/17/settings"


# Примечание: пустой PATCH, пояс null и несуществующий пояс — ошибка контракта
@pytest.mark.parametrize("raw", [{}, {"tz": None}, {"tz": "Mars/Base"}])
def test_settingsInvalid(raw: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        UpdateUserSettingsRequest.fromWire(raw)


# Примечание: ответ настроек с несуществующим поясом — ошибка, а не тихий дефолт
def test_settingsResponseInvalidTz() -> None:
    with pytest.raises(ValidationError):
        UserSettingsResponse.fromWire({**_example(SETTINGS_RESPONSE), "tz": "Mars/Base"})
