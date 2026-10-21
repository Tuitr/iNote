"""Проверки готовности: отвечают ли PostgreSQL и Redis."""

import asyncio
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from libs.contracts.health import CheckStatus, ReadinessResponse

# Без ограничения по времени /health/ready зависает, пока недоступная БД
# не ответит отказом, и оркестратор считает сервис зависшим, а не неготовым.
CHECK_TIMEOUT_SECONDS = 2.0

logger = logging.getLogger(__name__)


async def check_postgres(engine: AsyncEngine) -> CheckStatus:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
    except (SQLAlchemyError, OSError, TimeoutError) as error:
        logger.warning("PostgreSQL не отвечает: %s", type(error).__name__)
        return "fail"
    return "ok"


async def check_redis(client: Redis) -> CheckStatus:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await client.ping()
    except (RedisError, OSError, TimeoutError) as error:
        logger.warning("Redis не отвечает: %s", type(error).__name__)
        return "fail"
    return "ok"


async def check_readiness(engine: AsyncEngine, redis_client: Redis) -> ReadinessResponse:
    postgres_status, redis_status = await asyncio.gather(
        check_postgres(engine), check_redis(redis_client)
    )
    checks = {"postgres": postgres_status, "redis": redis_status}
    is_ready = all(status == "ok" for status in checks.values())
    return ReadinessResponse(status="ok" if is_ready else "degraded", checks=checks)
