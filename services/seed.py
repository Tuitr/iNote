"""Залить демо-данные на аккаунт: python -m services.seed --max-user-id <id>.

Только вручную, на стенде автоматически не запускается. Пользователь находится по max_user_id
или создаётся, как при первом сообщении боту. Повторный запуск ничего не дублирует.
"""

import argparse
import asyncio
import logging

from libs.core.config import load_default_tz, load_settings
from libs.core.log_setup import configure_logging
from libs.core.resources import create_db_engine, create_redis_client, create_session_factory
from libs.core.seed import seed_demo
from libs.core.services.time import now_utc
from libs.core.services.users import UserService

logger = logging.getLogger(__name__)


async def main(max_user_id: int) -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)
    redis = create_redis_client(settings.redis_url)
    try:
        session_factory = create_session_factory(engine)
        users = UserService(session_factory, load_default_tz(), redis)
        user = await users.resolve(max_user_id, None, None)
        result = await seed_demo(session_factory, redis, user.user_id, now_utc())
        logger.info(
            "Демо-данные для max_user_id=%s: создано %d, уже были %d",
            max_user_id,
            result.created,
            result.already_there,
        )
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Демо-данные для показа")
    parser.add_argument("--max-user-id", type=int, required=True, help="id пользователя в MAX")
    asyncio.run(main(parser.parse_args().max_user_id))
