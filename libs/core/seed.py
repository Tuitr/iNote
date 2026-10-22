"""Демо-данные для показа: месяц записей одного пользователя и немного вперёд.

Записи кладутся репозиторием, а не через ItemService: сервис отклоняет время в прошлом,
а для демо нужна именно история. Ключи идемпотентности постоянные (`seed:<n>`), поэтому
повторный запуск ничего не дублирует. Только ручной запуск (python -m services.seed).
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.core.repositories.items import ItemRepo, NewItem
from libs.core.services.items import require_user, reschedule
from libs.core.services.time import local_midnight_utc, local_time_utc, to_user_tz
from libs.core.services.versioning import bump_version


@dataclass(frozen=True)
class DemoItem:
    kind: str
    title: str
    # День относительно сегодняшнего в поясе пользователя; None — запись без срока.
    day: int | None
    # None при заданном day — на весь день.
    at: time | None = None
    participants: list[str] = field(default_factory=list)
    search_hints: str | None = None
    status: str = "open"
    is_deleted: bool = False
    rrule: str | None = None


DEMO_ITEMS = (
    DemoItem(
        "event",
        "Встреча с Иваном по бюджету",
        -28,
        time(15),
        ["Иван Петров"],
        "финансы смета деньги расходы планирование",
    ),
    DemoItem(
        "task",
        "Купить подарок маме",
        -25,
        None,
        [],
        "день рождения сувенир праздник",
        status="done",
    ),
    DemoItem(
        "reminder",
        "Позвонить Ивану насчёт договора",
        -21,
        time(10),
        [],
        "контракт соглашение юрист подпись",
        status="done",
    ),
    DemoItem(
        "event",
        "Созвон с дизайнером Мариной",
        -20,
        time(12),
        ["Марина"],
        "макеты интерфейс дизайн UI",
    ),
    DemoItem("task", "Записаться к стоматологу", -18, None, [], "зубной врач зубы клиника пломба"),
    DemoItem("task", "Старый черновик заметки", -15, None, [], is_deleted=True),
    DemoItem(
        "event",
        "Ретроспектива спринта",
        -14,
        time(17),
        ["Иван Петров", "Марина", "Олег"],
        "команда итоги обсуждение работа",
    ),
    DemoItem(
        "task", "Оплатить интернет", -12, None, [], "провайдер счёт платёж связь", status="done"
    ),
    DemoItem("event", "Обед с Олегом", -10, time(13), ["Олег"], "ресторан кафе еда"),
    DemoItem(
        "reminder", "Продлить страховку машины", -7, time(9), [], "ОСАГО КАСКО автомобиль полис"
    ),
    DemoItem(
        "task", "Подготовить отчёт для Ивана", -5, None, [], "документ квартальный итоги работа"
    ),
    DemoItem("event", "Йога", -3, time(8), [], "спорт тренировка зал здоровье"),
    DemoItem(
        "task", "Почитать «Чистую архитектуру»", None, None, [], "книга программирование чтение"
    ),
    DemoItem("reminder", "Забрать посылку", 1, time(18), [], "почта пункт выдачи доставка"),
    DemoItem(
        "event",
        "Встреча с Иваном: итоги квартала",
        2,
        time(15),
        ["Иван Петров"],
        "отчёт квартал результаты работа",
    ),
    DemoItem("task", "Купить продукты на неделю", 3, None, [], "магазин еда список покупок"),
    DemoItem("event", "Приём у терапевта", 5, time(11), [], "врач поликлиника здоровье"),
    DemoItem(
        "event",
        "Планёрка",
        None,
        time(9),
        ["Иван Петров", "Марина", "Олег"],
        "стендап летучка команда",
        rrule="FREQ=WEEKLY;BYDAY=MO",
    ),
)


@dataclass(frozen=True)
class SeedResult:
    created: int
    already_there: int


async def seed_demo(
    session_factory: async_sessionmaker[AsyncSession], redis: Redis, user_id: int, now: datetime
) -> SeedResult:
    created = 0
    async with session_factory() as session, session.begin():
        user = await require_user(session, user_id)
        items = ItemRepo(session)
        today = to_user_tz(now, user.tz).date()
        for number, demo in enumerate(DEMO_ITEMS, start=1):
            new = _new_item(
                demo,
                f"seed:{number}",
                today,
                user.tz,
                user.default_event_min,
                user.remind_before_min,
            )
            row = await items.insert_if_absent(user_id, new)
            if row is None:
                continue
            created += 1
            changes: dict[str, object] = {}
            if demo.status != "open":
                changes["status"] = demo.status
            if demo.is_deleted:
                changes["deleted_at"] = now
            if changes:
                row = await items.update(user_id, row.id, changes)
            await reschedule(session, row, now)
    if created:
        await bump_version(redis, user_id)
    return SeedResult(created=created, already_there=len(DEMO_ITEMS) - created)


def _new_item(
    demo: DemoItem,
    idempotency_key: str,
    today: date,
    tz: str,
    event_min: int,
    remind_before_min: int,
) -> NewItem:
    day = (
        _next_monday(today)
        if demo.rrule
        else (None if demo.day is None else today + timedelta(days=demo.day))
    )
    starts_at = remind_at = ends_at = None
    all_day = False
    if day is not None and demo.at is None:
        starts_at, all_day = local_midnight_utc(day, tz), True
    elif day is not None and demo.at is not None:
        moment = local_time_utc(day, demo.at, tz)
        if demo.kind == "reminder":
            remind_at = moment
        else:
            starts_at = moment
    if demo.kind == "event" and starts_at is not None and not all_day:
        ends_at = starts_at + timedelta(minutes=event_min)
        remind_at = starts_at - timedelta(minutes=remind_before_min)
    return NewItem(
        kind=demo.kind,
        title=demo.title,
        notes=None,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        remind_at=remind_at,
        rrule=demo.rrule,
        participants=demo.participants,
        source_text=None,
        idempotency_key=idempotency_key,
        search_hints=demo.search_hints,
    )


def _next_monday(today: date) -> date:
    return today + timedelta(days=7 - today.weekday())
