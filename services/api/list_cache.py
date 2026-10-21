"""Кеш ответов GET /v1/items в Redis: ключ va:v1:api:list:{user_id}:{ver}:{hash}, TTL 5 минут (J7).

Версия данных — часть ключа: как только данные пользователя меняются, версия растёт, и старые
ответы больше не находятся. Чистить кеш при изменениях не нужно, он истекает сам.
"""

import hashlib
import json
from typing import Any

from redis.asyncio import Redis

LIST_CACHE_TTL_SECONDS = 300


def cache_key(user_id: int, version: int, params: dict[str, Any]) -> str:
    """Ключ для одного набора параметров списка; одинаковые запросы попадают в один ключ."""
    canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return f"va:v1:api:list:{user_id}:{version}:{digest}"


async def read_cached(redis: Redis, key: str) -> bytes | None:
    return await redis.get(key)


async def store(redis: Redis, key: str, body: bytes) -> None:
    await redis.set(key, body, ex=LIST_CACHE_TTL_SECONDS)
