"""J8 · agent (C) → mcp-контейнер (D): users/resolve и PATCH settings. Владелец D.

Зеркало моделей D (libs/contracts/internal.py, libs/contracts/settings.py) для бота и агента:
импортировать модели D они не могут (их нет в образах C, это запрещает import-linter). Поэтому
поля здесь — подмножество того, что отдаёт D, а тест libs/contracts/tests/test_users.py
разбирает этим зеркалом примеры D из contracts/examples/j8 — разойдутся, тест покраснеет.

Агент узнаёт внутренний user_id и пояс по max_user_id; при первом обращении D создаёт пользователя
(created=true). Вызов идемпотентен по max_user_id — повтор не создаёт второго пользователя.
Агент кеширует ответ в va:v1:agent:user:{max_user_id} на 1 ч. Читатель игнорирует незнакомые поля.

PATCH /internal/v1/users/{user_id}/settings — смена пояса из /settings. D отвечает настройками
целиком (UserSettings), а не пользователем; после ответа агент сбрасывает кеш пользователя.
"""

from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from common.wire import WireModel

RESOLVE_PATH: Final = "/internal/v1/users/resolve"
SETTINGS_PATH: Final = "/internal/v1/users/{user_id}/settings"
DEFAULT_EVENT_MIN: Final = 60
NAME_MAX_LENGTH: Final = 256


# Примечание: кто пишет: max_user_id и чат из апдейта MAX, имя — для профиля при создании
class ResolveUserRequest(WireModel):
    maxUserId: int = Field(gt=0)
    maxChatId: int
    name: str | None = Field(default=None, max_length=NAME_MAX_LENGTH)


# Примечание: настройки пользователя (users.settings у D); неизвестные ключи игнорируются
class UserSettingsWire(WireModel):
    defaultEventMin: int = Field(default=DEFAULT_EVENT_MIN, ge=1)
    voiceReplies: bool = False


# Примечание: ответ D: внутренний user_id (users.id, bigserial), пояс, настройки, создан ли сейчас
class ResolveUserResponse(WireModel):
    userId: int = Field(gt=0)
    tz: str
    settings: UserSettingsWire = Field(default_factory=UserSettingsWire)
    created: bool = False

    # Примечание: пояс должен существовать — от него считаются «сегодня» и «завтра»
    @field_validator("tz")
    @classmethod
    def _checkTz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"неизвестный часовой пояс: {value}"
            raise ValueError(msg) from exc
        return value


# Примечание: ответ PATCH настроек у D — настройки целиком; user_id в нём нет
class UserSettingsResponse(WireModel):
    tz: str
    defaultEventMin: int = Field(default=DEFAULT_EVENT_MIN, ge=1)
    voiceReplies: bool = False

    # Примечание: пояс должен существовать — от него считаются «сегодня» и «завтра»
    @field_validator("tz")
    @classmethod
    def _checkTz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"неизвестный часовой пояс: {value}"
            raise ValueError(msg) from exc
        return value


# Примечание: путь PATCH настроек; user_id — внутренний (из resolve), не из кнопки
def settingsPath(userId: int) -> str:
    return SETTINGS_PATH.format(user_id=userId)


# Примечание: что поменять: пока только пояс (голосовых ответов в продукте нет — решение команды,
# S3); новые поля — добавлением, необязательными
class UpdateUserSettingsRequest(WireModel):
    tz: str

    # Примечание: пояс — только существующий IANA (белый список — у агента)
    @field_validator("tz")
    @classmethod
    def _checkTz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"неизвестный часовой пояс: {value}"
            raise ValueError(msg) from exc
        return value

    # Примечание: тело PATCH на проводе
    def toPatch(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude_none=True, mode="json")
