from datetime import date
from typing import Final

from agent.domain.user import UserId
from common.redis import KeyedRedis, RedisKey

# docs/redis-keys.md: va:v1:agent:llm_day:{user_id}.{yyyymmdd}, TTL 48 ч — сутки пользователя
# в любом поясе плюс запас; счётчик за прошлый день просто истекает
QUOTA_TTL_S: Final = 48 * 3600


# Примечание: дневной лимит ходов с LLM на пользователя (INCR с TTL): платный GigaChat не
# должен уходить на одного пользователя или на зацикленного клиента
class RedisLlmQuota:
    def __init__(self, store: KeyedRedis, limit: int, ttlS: int = QUOTA_TTL_S) -> None:
        self._store = store
        self._limit = limit
        self._ttlS = ttlS

    # Примечание: True — ход в пределах лимита; день — в поясе пользователя
    async def allow(self, userId: UserId, day: date) -> bool:
        key = RedisKey("agent", "llm_day", f"{userId}.{day:%Y%m%d}")
        count = await self._store.incrementWithTtl(key, ttlS=self._ttlS)
        return count <= self._limit
