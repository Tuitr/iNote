from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from agent.domain.user import Caller, UserContext, UserId
from contracts.agent_side import ItemBrief, JsonObject, ToolExecutor, Turn, TurnResult
from contracts.reply import ActionRequest, Reply


# Примечание: контекст диалога между ходами: история, фокус и непрозрачный pending (формат B)
@dataclass(frozen=True)
class DialogContext:
    history: tuple[Turn, ...] = ()
    focus: tuple[ItemBrief, ...] = ()
    pending: JsonObject | None = field(default=None)


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


# Примечание: перевод итога хода в Reply; все тексты — в шаблонах presentation
class ReplyRenderer(Protocol):
    # Примечание: Reply по итогу хода Brain
    def render(self, result: TurnResult, user: UserContext, traceId: str) -> Reply: ...

    # Примечание: Reply, если лок пользователя занят
    def busy(self, traceId: str) -> Reply: ...

    # Примечание: Reply-ошибка по коду (upstream_timeout, internal, …)
    def failure(self, errorCode: str, traceId: str) -> Reply: ...


# Примечание: источник фиксированных ответов для /v1/actions — заглушка S0 до S2
class ReplySource(Protocol):
    # Примечание: ответ на кнопку или команду без LLM
    async def forAction(self, request: ActionRequest) -> Reply: ...
