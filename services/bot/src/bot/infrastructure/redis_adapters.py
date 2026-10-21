from typing import Final

from bot.domain.updates import MaxUserId, UpdateId
from common.redis import KeyedRedis, RedisKey

# docs/redis-keys.md: va:v1:bot:upd:{update_id} — 24 ч; va:v1:bot:rl:{max_user_id} — 60 с
DEDUP_TTL_S: Final = 24 * 60 * 60
RATE_WINDOW_S: Final = 60
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

    # Примечание: счётчик растёт и сверх лимита — окно не продлевается, сбросится по TTL
    async def allow(self, maxUserId: MaxUserId) -> bool:
        key = RedisKey("bot", "rl", str(maxUserId))
        count = await self._store.incrementWithTtl(key, ttlS=self._windowS)
        return count <= self._limit
