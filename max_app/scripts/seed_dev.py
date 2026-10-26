"""Тестовые записи для dev-пользователя мини-приложения в локальном api.

Запуск из корня репозитория, когда поднят compose:
    docker compose exec -T api python - < max_app/scripts/seed_dev.py

Пользователь — тот же, под которым фронт входит вне MAX (devInitData в src/max/bridge.ts).
Записи раскладываются относительно сегодняшнего дня. Перед заполнением старые записи
dev-пользователя удаляются, поэтому скрипт можно запускать повторно.
"""

import asyncio
import secrets
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete

from libs.contracts.tools import Caller, CompleteItemArgs, CreateEventArgs, CreateTaskArgs
from libs.core.config import load_database_url, load_default_tz, load_settings
from libs.core.db.models import ItemRow
from libs.core.resources import create_db_engine, create_redis_client, create_session_factory
from libs.core.services.items import ItemService
from libs.core.services.users import UserService
from libs.core.services.versioning import bump_version

DEV_MAX_USER_ID = 123456789


async def main() -> None:
    engine = create_db_engine(load_database_url())
    redis = create_redis_client(load_settings().redis_url)
    sessions = create_session_factory(engine)
    try:
        user = await UserService(sessions, load_default_tz(), redis).resolve(
            DEV_MAX_USER_ID, None, "Разработчик"
        )
        tz = ZoneInfo(user.settings.tz)
        today = datetime.now(tz).date()
        async with sessions.begin() as session:
            await session.execute(delete(ItemRow).where(ItemRow.user_id == user.user_id))
        # Новая версия данных: у фронта старый ETag перестаёт давать 304.
        await bump_version(redis, user.user_id)

        # Сервис не создаёт записи в прошлом, а нам нужны и вчера, и утро сегодня:
        # для него «сейчас» — полночь вчерашнего дня.
        yesterday = datetime.combine(today - timedelta(days=1), datetime.min.time(), tz)
        items = ItemService(sessions, redis, clock=lambda: yesterday)

        def caller() -> Caller:
            return Caller(user_id=user.user_id, tz=user.settings.tz, trace_id=secrets.token_hex(16))

        def at(day: date, hour: int, minute: int = 0) -> datetime:
            return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)

        def day(offset: int) -> date:
            return today + timedelta(days=offset)

        async def event(title: str, start: datetime, minutes: int, *people: str) -> None:
            await items.create_event(
                CreateEventArgs(
                    caller=caller(),
                    title=title,
                    starts_at=start,
                    ends_at=start + timedelta(minutes=minutes),
                    participants=list(people),
                    allow_conflict=True,
                )
            )

        async def task(title: str, starts_at: date | datetime | None = None) -> str:
            result = await items.create_task(
                CreateTaskArgs(caller=caller(), title=title, starts_at=starts_at)
            )
            assert result.item is not None
            return str(result.item.id)

        async def reminder(title: str, remind_at: datetime) -> None:
            await items.create_task(
                CreateTaskArgs(caller=caller(), title=title, kind="reminder", remind_at=remind_at)
            )

        await task("День рождения Маши", day(0))
        await reminder("Позвонить Ивану", at(day(0), 10))
        await event("Встреча с дизайнером", at(day(0), 11), 60, "Дизайнер")
        await task("Купить продукты на неделю", at(day(0), 18))
        done = await task("Отправить отчёт", at(day(0), 9))
        await items.complete_item(CompleteItemArgs(caller=caller(), id=done))
        await task("Подготовить презентацию", at(day(-1), 15))
        await event("Тренировка", at(day(1), 8), 90)
        await task("Оплатить интернет", day(1))
        await event("Стоматолог", at(day(3), 16), 60)
        await event("Планёрка", at(day(7), 10), 30, "Команда")
        await task("Сдать квартальный отчёт", day(12))
        await event("Ужин с родителями", at(day(20), 19), 120, "Мама", "Папа")
        print(f"Готово: user_id={user.user_id}, пояс {user.settings.tz}, сегодня {today}")
    finally:
        await redis.aclose()
        await engine.dispose()


asyncio.run(main())
