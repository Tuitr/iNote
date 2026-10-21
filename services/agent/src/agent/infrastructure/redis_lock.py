import asyncio
import secrets
from typing import Final

from agent.domain.user import UserId
from common.redis import KeyedRedis, RedisKey

# docs/redis-keys.md: va:v1:agent:lock:{user_id}, TTL 60 с — дольше бюджета хода (40 с)
LOCK_TTL_S: Final = 60
_POLL_INITIAL_S: Final = 0.05
_POLL_MAX_S: Final = 0.5


# Примечание: лок пользователя в Redis: SET NX EX с токеном владельца, снять может только владелец
class RedisUserLock:
    # Примечание: TTL страхует от вечного лока, если процесс упал посреди хода
    def __init__(self, store: KeyedRedis, ttlS: int = LOCK_TTL_S) -> None:
        self._store = store
        self._ttlS = ttlS

    # Примечание: ждём освобождения с растущей паузой, но не дольше waitS
    async def acquire(self, userId: UserId, waitS: float) -> str | None:
        key = _lockKey(userId)
        token = secrets.token_hex(16)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + waitS
        pause = _POLL_INITIAL_S
        while True:
            if await self._store.setIfAbsent(key, token, ttlS=self._ttlS):
                return token
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            await asyncio.sleep(min(pause, remaining))
            pause = min(pause * 2, _POLL_MAX_S)

    # Примечание: если лок истёк и его взял другой ход — не трогаем
    async def release(self, userId: UserId, token: str) -> bool:
        return await self._store.deleteIfEquals(_lockKey(userId), token)


# Примечание: ключ лока по внутреннему user_id
def _lockKey(userId: UserId) -> RedisKey:
    return RedisKey("agent", "lock", str(userId))
