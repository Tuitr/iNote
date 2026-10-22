"""Контейнер scheduler: цикл reclaim_expired → claim_due → deliver раз в SCHED_INTERVAL_SECONDS.

Без веб-сервера. Упадёт посреди отправки — аренда истечёт, и уведомление заберёт следующий
проход этой или другой реплики.
"""

import asyncio
import logging

import httpx
from sqlalchemy.exc import SQLAlchemyError

from libs.core.config import load_scheduler_settings
from libs.core.log_setup import configure_logging
from libs.core.resources import create_db_engine, create_session_factory
from libs.core.services.reminders import ReminderService
from services.scheduler.notify import NOTIFY_TIMEOUT_SECONDS, BotNotifier

logger = logging.getLogger(__name__)


async def run_cycle(reminders: ReminderService, notifier: BotNotifier) -> None:
    reclaimed = await reminders.reclaim_expired()
    if reclaimed:
        logger.warning("Вернул в очередь %d уведомлений с истёкшей арендой", reclaimed)
    for reminder in await reminders.claim_due():
        delivery, reason = await notifier.notify(reminder)
        await reminders.finish(reminder, delivery, reason)


async def main() -> None:
    settings = load_scheduler_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)
    reminders = ReminderService(create_session_factory(engine))
    try:
        async with httpx.AsyncClient(
            base_url=settings.bot_url, timeout=NOTIFY_TIMEOUT_SECONDS
        ) as client:
            notifier = BotNotifier(client, settings.service_token)
            while True:
                try:
                    await run_cycle(reminders, notifier)
                except SQLAlchemyError:
                    # База недоступна: не роняем контейнер, пробуем на следующем проходе.
                    logger.exception("Проход Scheduler не выполнен")
                await asyncio.sleep(settings.interval_seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
