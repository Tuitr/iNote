"""Версия данных пользователя в Redis: по ней API отдаёт ETag и понимает, что данные изменились."""

import asyncio
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

# Создание записи не должно зависать, если Redis не отвечает: запись уже сохранена.
BUMP_TIMEOUT_SECONDS = 1.0

logger = logging.getLogger(__name__)


async def bump_version(redis: Redis, user_id: int) -> None:
    """INCR va:v1:mcp:ver:{user_id} (ключ из реестра J7). Вызывать только после коммита.

    Ошибка Redis не пробрасывается: данные уже в базе, и агент не должен повторять вызов.
    Фронт увидит изменения позже, когда версия снова вырастет (решение Никиты, план S1).
    """
    try:
        async with asyncio.timeout(BUMP_TIMEOUT_SECONDS):
            await redis.incr(f"va:v1:mcp:ver:{user_id}")
    except (RedisError, OSError, TimeoutError) as error:
        logger.error("Версия данных user_id=%s не увеличена: %s", user_id, type(error).__name__)
