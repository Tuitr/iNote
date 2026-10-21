"""J8 · agent (C) → mcp-контейнер (D): POST /internal/v1/users/resolve. Владелец D, черновик C к G1.

Агент узнаёт внутренний user_id и пояс по max_user_id; при первом обращении D создаёт пользователя
(created=true). Вызов идемпотентен по max_user_id — повтор не создаёт второго пользователя.
Агент кеширует ответ в va:v1:agent:user:{max_user_id} на 1 ч. Читатель игнорирует незнакомые поля.
"""

from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from common.wire import WireModel

RESOLVE_PATH: Final = "/internal/v1/users/resolve"
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
