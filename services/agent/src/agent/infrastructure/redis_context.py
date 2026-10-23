import json
from typing import Final

from pydantic import BaseModel, TypeAdapter, ValidationError

from agent.application.ports import DialogContext
from agent.domain.user import UserId
from common.logging import getLogger
from common.redis import KeyedRedis, RedisKey
from contracts.agent_side import ItemBrief, Turn

# docs/redis-keys.md: ctx и focus — 2 ч, pending — 10 мин
CONTEXT_TTL_S: Final = 2 * 60 * 60
FOCUS_TTL_S: Final = 2 * 60 * 60
PENDING_TTL_S: Final = 10 * 60

_HISTORY: Final = TypeAdapter(list[Turn])
_FOCUS: Final = TypeAdapter(list[ItemBrief])

_log = getLogger(__name__)


# Примечание: ключ pending: непрозрачный pending Brain + варианты, ушедшие кнопками
class StoredPending(BaseModel):
    brain: dict[str, object]
    options: list[str] = []


_PENDING: Final = TypeAdapter(StoredPending)


# Примечание: контекст диалога в трёх ключах Redis; pending хранится как есть — формат решает B
class RedisDialogContextStore:
    # Примечание: работает поверх KeyedRedis — формат ключей и TTL проверяются там
    def __init__(self, store: KeyedRedis) -> None:
        self._store = store

    # Примечание: битый JSON в ключе не роняет ход — начинаем с пустого контекста этой части
    async def load(self, userId: UserId) -> DialogContext:
        history = await self._read(_key("ctx", userId), _HISTORY)
        focus = await self._read(_key("focus", userId), _FOCUS)
        stored = await self._read(_key("pending", userId), _PENDING)
        return DialogContext(
            history=tuple(history or []),
            focus=tuple(focus or []),
            pending=stored.brain if stored else None,
            options=tuple(stored.options) if stored else (),
        )

    # Примечание: pending=None удаляет ключ — незавершённого уточнения больше нет
    async def save(self, userId: UserId, context: DialogContext) -> None:
        await self._store.setValue(
            _key("ctx", userId), _dumpList(context.history), ttlS=CONTEXT_TTL_S
        )
        await self._store.setValue(
            _key("focus", userId), _dumpList(context.focus), ttlS=FOCUS_TTL_S
        )
        pendingKey = _key("pending", userId)
        if context.pending is None:
            await self._store.delete(pendingKey)
        else:
            stored = StoredPending(brain=context.pending, options=list(context.options))
            await self._store.setValue(pendingKey, stored.model_dump_json(), ttlS=PENDING_TTL_S)

    # Примечание: чтение и разбор одного ключа; None — ключа нет или он битый
    async def _read[T](self, key: RedisKey, adapter: TypeAdapter[T]) -> T | None:
        raw = await self._store.getValue(key)
        if raw is None:
            return None
        try:
            return adapter.validate_json(raw)
        except ValidationError:
            _log.warning("context_key_corrupted", key=key.entity)
            return None


# Примечание: ключ контекста по внутреннему user_id
def _key(entity: str, userId: UserId) -> RedisKey:
    return RedisKey("agent", entity, str(userId))


# Примечание: список моделей в JSON snake_case
def _dumpList(models: tuple[Turn, ...] | tuple[ItemBrief, ...]) -> str:
    return json.dumps([m.toWire() for m in models], ensure_ascii=False)
