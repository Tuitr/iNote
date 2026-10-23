import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from agent.application.item_time import ItemKind
from agent.application.reschedule import RescheduleOption
from agent.domain.user import Caller, UserContext, UserId
from contracts.agent_side import ItemBrief, JsonObject, ToolExecutor, Turn, TurnResult
from contracts.dialog_turns import DialogInputType
from contracts.reply import Reply


# Примечание: контекст диалога между ходами: история, фокус и непрозрачный pending (формат B)
@dataclass(frozen=True)
class DialogContext:
    history: tuple[Turn, ...] = ()
    focus: tuple[ItemBrief, ...] = ()
    pending: JsonObject | None = field(default=None)
    # варианты уточнения в том порядке, в каком ушли кнопками (pick_option.args.choice — индекс)
    options: tuple[str, ...] = ()


# Примечание: max_user_id → внутренний пользователь (J8 resolve, с кешем)
class UserResolver(Protocol):
    # Примечание: создаёт пользователя при первом обращении; недоступность — AppError upstream_*
    async def resolve(self, maxUserId: int, maxChatId: int, name: str | None) -> UserContext: ...


# Примечание: лок на пользователя — ходы одного пользователя по очереди
class UserLock(Protocol):
    # Примечание: токен владельца или None, если не дождались за waitS
    async def acquire(self, userId: UserId, waitS: float) -> str | None: ...

    # Примечание: снимает лок, только если он всё ещё наш (по токену)
    async def release(self, userId: UserId, token: str) -> bool: ...


# Примечание: хранилище контекста диалога (Redis, TTL по реестру)
class DialogContextStore(Protocol):
    # Примечание: пустой контекст, если ключей нет или истёк TTL
    async def load(self, userId: UserId) -> DialogContext: ...

    # Примечание: сохранить историю, фокус и pending после хода
    async def save(self, userId: UserId, context: DialogContext) -> None: ...


# Примечание: источник «сейчас» — в тестах заморожен, в коде всегда aware UTC
class Clock(Protocol):
    # Примечание: текущий момент с часовым поясом
    def now(self) -> datetime: ...


# Примечание: исполнитель инструментов на один ход — счётчик вызовов не делится между ходами
class ToolExecutorFactory(Protocol):
    # Примечание: новый ToolExecutor с caller этого хода
    def forTurn(self, caller: Caller) -> ToolExecutor: ...


# Примечание: что сказал пользователь: текст (как есть), голос (распознанный текст) или кнопка
@dataclass(frozen=True)
class UserInput:
    text: str
    inputType: DialogInputType


# Примечание: запись истории диалога после хода (J8 dialog-turns): пара реплик и время ответа
@dataclass(frozen=True)
class DialogEntry:
    userId: UserId
    traceId: str
    said: UserInput
    answer: str
    itemIds: tuple[uuid.UUID, ...]
    latencyMs: int


# Примечание: история диалога у D; запись — в фоне, ответ пользователю её не ждёт
class DialogLog(Protocol):
    # Примечание: не бросает и не блокирует: сбой записи — только лог (история не критична)
    def record(self, entry: DialogEntry) -> None: ...


# Примечание: короткие ответы действий без LLM; тексты — в шаблонах ([notices] в ru.toml)
class Notice(StrEnum):
    PENDING_EXPIRED = "pending_expired"
    CANCELLED = "cancelled"
    ALREADY_UNDONE = "already_undone"
    ALREADY_COMPLETED = "already_completed"
    UNKNOWN_ACTION = "unknown_action"


# Примечание: приветствие («Начать», /start) или справка (/help)
class Welcome(StrEnum):
    START = "start"
    HELP = "help"


# Примечание: что поменять в настройках: None — не трогать
@dataclass(frozen=True)
class SettingsChange:
    tz: str | None = None
    voiceReplies: bool | None = None


# Примечание: запись настроек пользователя (J8 PATCH у D) и сброс кеша пользователя агента
class SettingsWriter(Protocol):
    # Примечание: user_id — из resolve (session.user), не из кнопки; ответ — пользователь после
    # изменения; недоступность J8 — AppError upstream_*
    async def update(self, user: UserContext, change: SettingsChange) -> UserContext: ...


# Примечание: перевод итога хода в Reply; все тексты — в шаблонах presentation
class ReplyRenderer(Protocol):
    # Примечание: Reply по итогу хода Brain; heard — распознанный текст голосового («Распознал: …»)
    def render(
        self, result: TurnResult, user: UserContext, traceId: str, *, heard: str | None = None
    ) -> Reply: ...

    # Примечание: Reply, если лок пользователя занят
    def busy(self, traceId: str) -> Reply: ...

    # Примечание: короткий ответ действия без LLM («Вопрос устарел», «Хорошо, отменил»)
    def notice(self, notice: Notice, traceId: str) -> Reply: ...

    # Примечание: вопрос «На когда перенести?» с вариантами-кнопками reschedule и «Отмена»
    def rescheduleQuestion(
        self,
        itemId: str,
        kind: ItemKind | None,
        options: Sequence[RescheduleOption],
        user: UserContext,
        traceId: str,
    ) -> Reply: ...

    # Примечание: «Напомню завтра, чт 22 октября, 10:00 / Позвонить Ивану» по ответу update_item
    def snoozed(self, result: TurnResult, user: UserContext, traceId: str) -> Reply: ...

    # Примечание: приветствие или справка с примерами фраз и кнопкой «Мой день» (open_app today)
    def welcome(self, kind: Welcome, traceId: str) -> Reply: ...

    # Примечание: /settings: текущий пояс и голосовые ответы, кнопки поясов и переключатель
    def settings(self, user: UserContext, traceId: str) -> Reply: ...

    # Примечание: подтверждение изменения: «Пояс: Екатеринбург (UTC+5)» или голосовые вкл/выкл
    def settingsChanged(self, user: UserContext, change: SettingsChange, traceId: str) -> Reply: ...

    # Примечание: Reply-ошибка по коду (upstream_timeout, internal, …)
    def failure(self, errorCode: str, traceId: str) -> Reply: ...
