import json
import re
import secrets
from typing import Final

from pydantic import TypeAdapter, ValidationError

from bot.application.ports import RateDecision, StoredAction
from bot.domain.updates import MaxUserId, UpdateId
from common.logging import getLogger
from common.redis import KeyedRedis, RedisKey
from contracts.reply import ACTION_PATTERN, ArgValue

# docs/redis-keys.md: va:v1:bot:upd:{update_id} — 24 ч; va:v1:bot:rl:{max_user_id} — 60 с;
# va:v1:bot:marker:poll — 24 ч (не дольше дедупа: повтор после рестарта отсекается дедупом)
DEDUP_TTL_S: Final = 24 * 60 * 60
RATE_WINDOW_S: Final = 60
MARKER_TTL_S: Final = DEDUP_TTL_S
DEFAULT_RATE_LIMIT: Final = 20
# va:v1:bot:cb:{key} — 24 ч, как живёт клавиатура в чате (править сообщение MAX даёт 24 ч)
CALLBACK_TTL_S: Final = 24 * 60 * 60
CALLBACK_KEY_BYTES: Final = 12
# token_urlsafe(12) — 16 символов [A-Za-z0-9_-]; чужой payload (старый, подделка) — мимо Redis
_CALLBACK_KEY_RE: Final = re.compile(r"^[A-Za-z0-9_-]{16}$")
_ACTION_RE: Final = re.compile(ACTION_PATTERN)
_ARGS: Final = TypeAdapter(dict[str, ArgValue])

_log = getLogger(__name__)


# Примечание: дедуп апдейтов: SET NX с TTL 24 ч — повтор в пределах суток отсекается
class RedisUpdateDeduplicator:
    # Примечание: работает поверх KeyedRedis — ключ и TTL проверяются там
    def __init__(self, store: KeyedRedis, ttlS: int = DEDUP_TTL_S) -> None:
        self._store = store
        self._ttlS = ttlS

    # Примечание: True, если ключа ещё не было
    async def isFirstSeen(self, updateId: UpdateId) -> bool:
        return await self._store.setIfAbsent(RedisKey("bot", "upd", updateId), "1", ttlS=self._ttlS)


# Примечание: фиксированное окно 60 с на пользователя: INCR + EXPIRE NX
class RedisRateLimiter:
    # Примечание: limit — сообщений за окно (стандарт: 20 в минуту)
    def __init__(
        self, store: KeyedRedis, limit: int = DEFAULT_RATE_LIMIT, windowS: int = RATE_WINDOW_S
    ) -> None:
        self._store = store
        self._limit = limit
        self._windowS = windowS

    # Примечание: окно не продлевается; notify только на первом превышении (count == limit + 1)
    async def check(self, maxUserId: MaxUserId) -> RateDecision:
        key = RedisKey("bot", "rl", str(maxUserId))
        count = await self._store.incrementWithTtl(key, ttlS=self._windowS)
        if count <= self._limit:
            return RateDecision(allowed=True)
        return RateDecision(allowed=False, notify=count == self._limit + 1)


# Примечание: marker long polling в Redis — переживает рестарт и падение процесса (режим real)
class RedisMarkerStore:
    # Примечание: один ключ на бота; TTL обновляется при каждой фиксации
    def __init__(self, store: KeyedRedis, ttlS: int = MARKER_TTL_S) -> None:
        self._store = store
        self._ttlS = ttlS
        self._key = RedisKey("bot", "marker", "poll")

    # Примечание: None — ключа нет (первый запуск или истёк TTL)
    async def load(self) -> int | None:
        value = await self._store.getValue(self._key)
        return None if value is None else int(value)

    # Примечание: SET с TTL после обработки страницы
    async def save(self, marker: int) -> None:
        await self._store.setValue(self._key, str(marker), ttlS=self._ttlS)


# Примечание: действие за callback-кнопкой: payload кнопки — случайный ключ, действие — в Redis
class RedisCallbackStore:
    # Примечание: ключ va:v1:bot:cb:{key}, TTL 24 ч
    def __init__(self, store: KeyedRedis, ttlS: int = CALLBACK_TTL_S) -> None:
        self._store = store
        self._ttlS = ttlS

    # Примечание: новый ключ на каждую кнопку — нажатие нельзя подобрать или подделать
    async def save(self, action: StoredAction) -> str:
        key = secrets.token_urlsafe(CALLBACK_KEY_BYTES)
        payload = json.dumps({"action": action.action, "args": action.args}, ensure_ascii=False)
        await self._store.setValue(_callbackKey(key), payload, ttlS=self._ttlS)
        return key

    # Примечание: None — ключ не нашего формата, истёк или в Redis лежит не то
    async def load(self, key: str) -> StoredAction | None:
        if not _CALLBACK_KEY_RE.fullmatch(key):
            return None
        raw = await self._store.getValue(_callbackKey(key))
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            action = str(data["action"])
            args = _ARGS.validate_python(data.get("args", {}))
        except (ValueError, KeyError, TypeError, ValidationError):
            _log.warning("callback_corrupted")
            return None
        if not _ACTION_RE.fullmatch(action):
            return None
        return StoredAction(action=action, args=args)


# Примечание: ключ Redis кнопки
def _callbackKey(key: str) -> RedisKey:
    return RedisKey("bot", "cb", key)
