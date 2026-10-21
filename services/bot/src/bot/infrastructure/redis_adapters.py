from typing import Final

from bot.application.ports import RateDecision
from bot.domain.updates import MaxUserId, UpdateId
from common.redis import KeyedRedis, RedisKey

# docs/redis-keys.md: va:v1:bot:upd:{update_id} — 24 ч; va:v1:bot:rl:{max_user_id} — 60 с;
# va:v1:bot:marker:poll — 24 ч (не дольше дедупа: повтор после рестарта отсекается дедупом)
DEDUP_TTL_S: Final = 24 * 60 * 60
RATE_WINDOW_S: Final = 60
MARKER_TTL_S: Final = DEDUP_TTL_S
DEFAULT_RATE_LIMIT: Final = 20


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
