"""Окружение Alembic: накатывает миграции на базу из DATABASE_URL через asyncpg."""

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from libs.core.config import load_database_url
from libs.core.db.models import Base
from libs.core.log_setup import configure_logging
from libs.core.resources import create_db_engine


def _run_migrations(connection: Connection) -> None:
    # Модели нужны alembic check: он сверяет их со схемой, которую построили миграции.
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    engine = create_db_engine(load_database_url())
    try:
        async with engine.connect() as connection:
            # Alembic синхронный, а драйвер asyncpg асинхронный: run_sync выполняет
            # синхронную функцию поверх асинхронного соединения.
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError(
        "Офлайн-режим Alembic (--sql) не используется: миграции идут только на живую базу"
    )

# Шаги миграции должны быть видны в логе контейнера migrate при любом LOG_LEVEL.
configure_logging("INFO")
asyncio.run(_run_async_migrations())
