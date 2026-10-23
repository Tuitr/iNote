from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

type UpdateId = str
type MaxUserId = int
type MaxChatId = int

# Заголовок webhook с секретом подписки (dev.max.ru, POST /subscriptions) — имя, а не секрет
WEBHOOK_SECRET_HEADER: Final = "X-Max-Bot-Api-Secret"  # noqa: S105


# Примечание: вид входящего события; command — текст, начинающийся с «/»
class UpdateKind(StrEnum):
    TEXT = "text"
    VOICE = "voice"
    CALLBACK = "callback"
    BOT_STARTED = "bot_started"
    COMMAND = "command"


# Примечание: ссылка на голосовое; длительности и mime в схеме MAX нет — поля необязательные
@dataclass(frozen=True)
class VoiceRef:
    url: str
    token: str
    durationS: int | None = None
    mime: str | None = None


# Примечание: событие от MAX в терминах бота; формат MAX сюда не протекает
@dataclass(frozen=True)
class IncomingUpdate:
    updateId: UpdateId
    kind: UpdateKind
    maxUserId: MaxUserId
    maxChatId: MaxChatId | None
    text: str | None = None
    commandName: str | None = None
    callbackId: str | None = None
    callbackPayload: str | None = None
    startPayload: str | None = None
    voice: VoiceRef | None = None
    # Имя из профиля MAX и язык клиента — для J8 resolve и MessageRequest.locale
    userName: str | None = None
    locale: str | None = None


# Примечание: страница апдейтов и marker следующей страницы (фиксируется после обработки)
@dataclass(frozen=True)
class UpdateBatch:
    updates: tuple[IncomingUpdate, ...]
    marker: int | None


# сырой апдейт MAX → событие; None — неподдержанное, чужое или битое (причина — в логе)
type RawUpdateMapper = Callable[[Mapping[str, object]], IncomingUpdate | None]
