from dataclasses import dataclass
from typing import Final

type UserId = int

DEFAULT_EVENT_MIN: Final = 60


# Примечание: пользователь хода; user_id — внутренний (J8), в Brain и наружу не уходит
@dataclass(frozen=True)
class UserContext:
    userId: UserId
    maxUserId: int
    maxChatId: int
    tz: str
    defaultEventMin: int = DEFAULT_EVENT_MIN
    voiceReplies: bool = False


# Примечание: служебные данные для MCP (J3 caller); ключ идемпотентности добавляет исполнитель
@dataclass(frozen=True)
class Caller:
    userId: UserId
    tz: str
    traceId: str
