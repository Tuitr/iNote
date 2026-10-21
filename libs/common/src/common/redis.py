import re
from dataclasses import dataclass
from typing import Final, Self, override

from redis.asyncio import Redis

KEY_NAMESPACE: Final = "va"
KEY_VERSION: Final = "v1"
_SEGMENT_RE: Final = re.compile(r"^[a-z][a-z0-9_]*$")
# id без ':' — иначе ключ нельзя однозначно разобрать обратно
_ID_RE: Final = re.compile(r"^[A-Za-z0-9_.\-]+$")

# Единственные ключи без TTL (docs/redis-keys.md); остальные — только с TTL (правило 4)
NO_TTL_WHITELIST: Final = frozenset({("mcp", "ver")})


# Примечание: ключ Redis строго вида va:v1:<сервис>:<сущность>:<id> (J7)
@dataclass(frozen=True)
class RedisKey:
    service: str
    entity: str
    id: str

    # Примечание: проверяет сегменты при создании — кривой ключ не доживёт до Redis
    def __post_init__(self) -> None:
        for name, value in (("service", self.service), ("entity", self.entity)):
            if not _SEGMENT_RE.fullmatch(value):
                msg = f"RedisKey.{name}={value!r}: только [a-z0-9_], с буквы"
                raise ValueError(msg)
        if not _ID_RE.fullmatch(self.id):
            msg = f"RedisKey.id={self.id!r}: только [A-Za-z0-9_.-]"
            raise ValueError(msg)

    # Примечание: строка ключа для команд Redis
    @override
    def __str__(self) -> str:
        return f"{KEY_NAMESPACE}:{KEY_VERSION}:{self.service}:{self.entity}:{self.id}"

    # Примечание: можно ли писать этот ключ без TTL — только по белому списку реестра
    @property
    def allowsNoTtl(self) -> bool:
        return (self.service, self.entity) in NO_TTL_WHITELIST

    # Примечание: обратный разбор строки (для тестов и отладки)
    @classmethod
    def parse(cls, raw: str) -> Self:
        parts = raw.split(":")
        expectedParts = 5
        if len(parts) != expectedParts or parts[:2] != [KEY_NAMESPACE, KEY_VERSION]:
            msg = f"не ключ {KEY_NAMESPACE}:{KEY_VERSION}: {raw!r}"
            raise ValueError(msg)
        return cls(service=parts[2], entity=parts[3], id=parts[4])


# Примечание: ошибка записи ключа без TTL вне белого списка
class MissingTtlError(ValueError):
    pass


# Примечание: клиент redis-py asyncio; создаётся один раз в lifespan (правило 9)
def createRedis(url: str, *, socketTimeoutS: float = 2.0) -> Redis:
    return Redis.from_url(
        url,
        decode_responses=True,
        socket_timeout=socketTimeoutS,
        socket_connect_timeout=socketTimeoutS,
        health_check_interval=30,
    )


# Примечание: обёртка записи: ключи только RedisKey, TTL обязателен (кроме белого списка)
class KeyedRedis:
    # Примечание: сам клиент не создаёт — получает готовый из lifespan
    def __init__(self, client: Redis) -> None:
        self._client = client

    # Примечание: сырой клиент — для health и операций без записи
    @property
    def client(self) -> Redis:
        return self._client

    # Примечание: TTL проверяется до запроса; без TTL — только ключи из белого списка
    @staticmethod
    def _requireTtl(key: RedisKey, ttlS: int | None) -> None:
        if ttlS is None:
            if not key.allowsNoTtl:
                msg = f"{key}: TTL обязателен (docs/redis-keys.md)"
                raise MissingTtlError(msg)
        elif ttlS <= 0:
            msg = f"{key}: TTL должен быть > 0, получено {ttlS}"
            raise MissingTtlError(msg)

    # Примечание: SET с EX; ttlS=None допустим только для белого списка
    async def setValue(self, key: RedisKey, value: str, *, ttlS: int | None) -> None:
        self._requireTtl(key, ttlS)
        await self._client.set(str(key), value, ex=ttlS)

    # Примечание: SET NX EX — True, если ключа не было (дедуп апдейтов)
    async def setIfAbsent(self, key: RedisKey, value: str, *, ttlS: int) -> bool:
        self._requireTtl(key, ttlS)
        created = await self._client.set(str(key), value, ex=ttlS, nx=True)
        return bool(created)

    # Примечание: INCR + EXPIRE NX одной транзакцией — TTL ставится один раз на окно (лимиты)
    async def incrementWithTtl(self, key: RedisKey, *, ttlS: int) -> int:
        self._requireTtl(key, ttlS)
        async with self._client.pipeline(transaction=True) as pipe:
            # Команды в pipeline только ставятся в очередь; результат — в execute()
            _ = pipe.incr(str(key))
            _ = pipe.expire(str(key), ttlS, nx=True)
            count, _ = await pipe.execute()
        return int(count)

    # Примечание: чтение строки; None — ключа нет
    async def getValue(self, key: RedisKey) -> str | None:
        value = await self._client.get(str(key))
        return None if value is None else str(value)

    # Примечание: оставшийся TTL в секундах (-1 — без TTL, -2 — ключа нет)
    async def ttl(self, key: RedisKey) -> int:
        return int(await self._client.ttl(str(key)))

    # Примечание: удаление ключа; True — ключ был
    async def delete(self, key: RedisKey) -> bool:
        return bool(await self._client.delete(str(key)))
