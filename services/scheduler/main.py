"""Контейнер scheduler: reclaim → отмена устаревших → claim → deliver → schedule_next.

Проход раз в SCHED_INTERVAL_SECONDS, без веб-сервера. Упадёт посреди отправки — аренда
истечёт, и уведомление заберёт следующий проход этой или другой реплики.
"""

import asyncio
import logging
import tempfile
from pathlib import Path

import httpx
from sqlalchemy.exc import SQLAlchemyError

from libs.core.config import load_scheduler_settings
from libs.core.log_setup import configure_logging
from libs.core.resources import create_db_engine, create_redis_client, create_session_factory
from libs.core.services.reminders import ReminderService
from services.scheduler.notify import NOTIFY_TIMEOUT_SECONDS, BotNotifier

logger = logging.getLogger(__name__)

# Отметка «проход завершён»: по её свежести healthcheck в compose понимает, что цикл жив
# (веб-сервера у Scheduler нет). Застрял проход или лежит база — отметка стареет.
HEARTBEAT = Path(tempfile.gettempdir()) / "scheduler-heartbeat"


async def run_cycle(reminders: ReminderService, notifier: BotNotifier) -> None:
    reclaimed = await reminders.reclaim_expired()
    if reclaimed:
        logger.warning("Вернул в очередь %d уведомлений с истёкшей арендой", reclaimed)
    stale = await reminders.cancel_stale()
    if stale:
        logger.info("Отменено устаревших уведомлений: %d", stale)
    # Маленькими пачками, пока есть созревшие: аренда каждой пачки короткая.
    while (batch := await reminders.claim_due()).claimed:
        for reminder in batch.due:
            delivery, reason = await notifier.notify(reminder)
            await reminders.finish(reminder, delivery, reason)
    # После доставки: созревшее напоминание серии успевает уйти до того, как она переедет.
    moved = await reminders.schedule_next()
    if moved:
        logger.info("Серий перенесено на следующее вхождение: %d", moved)


async def main() -> None:
    settings = load_scheduler_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)
    redis = create_redis_client(settings.redis_url)
    reminders = ReminderService(create_session_factory(engine), redis)
    try:
        async with httpx.AsyncClient(
            base_url=settings.bot_url, timeout=NOTIFY_TIMEOUT_SECONDS
        ) as client:
            notifier = BotNotifier(client, settings.service_token)
            while True:
                try:
                    await run_cycle(reminders, notifier)
                    HEARTBEAT.touch()
                except SQLAlchemyError:
                    # База недоступна: не роняем контейнер, пробуем на следующем проходе.
                    logger.exception("Проход Scheduler не выполнен")
                await asyncio.sleep(settings.interval_seconds)
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
