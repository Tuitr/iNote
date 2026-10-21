"""Записи пользователя: создание задач, напоминаний и встреч, список за период.

Один метод — одна транзакция; версия данных в Redis растёт только после коммита (развилка 4.3-А).
"""

from datetime import date, datetime, time, timedelta

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.items import Item, ItemBrief
from libs.contracts.tools import (
    Caller,
    CreateEventArgs,
    CreateTaskArgs,
    DateRange,
    ListItemsArgs,
    ListItemsResult,
    MutationResult,
    TimeSlot,
)
from libs.core.db.models import ItemRow, UserRow
from libs.core.embedding import Embedder, embed_or_none, item_text
from libs.core.errors import InvalidArgumentsError, NotFoundError, PastTimeError
from libs.core.repositories.items import ItemRepo, NewItem
from libs.core.repositories.notifications import NotificationRepo
from libs.core.repositories.users import UserRepo
from libs.core.services.time import (
    days_range_utc,
    local_midnight_utc,
    local_time_utc,
    local_today,
    now_utc,
    to_user_tz,
    to_utc,
)
from libs.core.services.versioning import bump_version

# Модель считает «через минуту» от своего «сейчас», а вызов доходит до базы позже (черновик D).
PAST_TIME_TOLERANCE = timedelta(minutes=1)
# Свободные окна предлагаем в тот же день, в это время, не больше трёх (план S1, решение 9).
FREE_SLOTS_FROM = time(8, 0)
FREE_SLOTS_TO = time(22, 0)
MAX_FREE_SLOTS = 3
# Больше записей модели за раз не отдаём, остальное помечаем truncated (J3).
MAX_LIST_ITEMS = 30


class ItemService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        embedder: Embedder | None,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis
        # None — эмбеддера нет (libs/embed от A ещё нет): записи сохраняются без вектора.
        self._embedder = embedder

    async def create_task(self, args: CreateTaskArgs) -> MutationResult:
        caller = args.caller
        if args.kind == "reminder" and args.remind_at is None:
            raise InvalidArgumentsError("Для напоминания нужно время", {"missing": ["remind_at"]})
        starts_at, all_day = _start(args.starts_at, caller.tz)
        task = NewItem(
            kind=args.kind,
            title=args.title,
            notes=args.notes,
            starts_at=starts_at,
            ends_at=None,
            all_day=all_day,
            remind_at=None if args.remind_at is None else to_utc(args.remind_at),
            rrule=args.rrule,
            participants=[],
            source_text=args.source_text,
            idempotency_key=caller.idempotency_key,
            embedding=await self._embed(args.title, args.notes, [], args.source_text),
        )
        created = False
        async with self._session_factory() as session, session.begin():
            await require_user(session, caller.user_id)
            items = ItemRepo(session)
            row = await _replay(items, caller)
            if row is None:
                _reject_past("remind_at", args.remind_at, caller.tz)
                row, created = await _insert(session, items, caller, task)
        if created:
            await bump_version(self._redis, caller.user_id)
        return MutationResult(status="created", item=to_item(row, caller.tz))

    async def create_event(self, args: CreateEventArgs) -> MutationResult:
        caller = args.caller
        _check_event_end(args)
        embedding = await self._embed(args.title, args.notes, args.participants, args.source_text)
        created = False
        async with self._session_factory() as session, session.begin():
            user = await require_user(session, caller.user_id)
            items = ItemRepo(session)
            row = await _replay(items, caller)
            if row is None:
                event = _new_event(args, user, embedding)
                # ends_at нет только у встречи на весь день, а она не конфликтует (решение 10).
                if not args.allow_conflict and event.starts_at and event.ends_at:
                    conflict = await _conflict(items, caller, event.starts_at, event.ends_at)
                    if conflict is not None:
                        return conflict
                row, created = await _insert(session, items, caller, event)
        if created:
            await bump_version(self._redis, caller.user_id)
        return MutationResult(status="created", item=to_item(row, caller.tz))

    async def list_items(self, args: ListItemsArgs) -> ListItemsResult:
        caller = args.caller
        if args.to <= args.from_:
            # to не включается: период «с 22 по 22 октября» пуст, модель ошиблась с датами.
            raise InvalidArgumentsError(
                "Конец периода должен быть позже начала", {"invalid": ["to"]}
            )
        start, end = days_range_utc(args.from_, args.to, caller.tz)
        kinds = [kind.value for kind in args.kind] if args.kind else None
        statuses = None if args.status == "all" else [args.status]
        async with self._session_factory() as session, session.begin():
            await require_user(session, caller.user_id)
            # На одну запись больше лимита: так видно, что записей больше, чем отдаём.
            rows = await ItemRepo(session).list_in_range(
                caller.user_id, start, end, kinds, statuses, MAX_LIST_ITEMS + 1
            )
        return ListItemsResult(
            items=[to_item(row, caller.tz) for row in rows[:MAX_LIST_ITEMS]],
            range=DateRange(from_=args.from_, to=args.to),
            truncated=len(rows) > MAX_LIST_ITEMS,
        )

    async def _embed(
        self, title: str, notes: str | None, participants: list[str], source_text: str | None
    ) -> list[float] | None:
        # Считаем до транзакции: ожидание эмбеддера не должно держать соединение с базой.
        text = item_text(title, notes, participants, source_text)
        return await embed_or_none(self._embedder, text, "document")


def free_slots(
    busy: list[tuple[datetime, datetime]],
    duration: timedelta,
    window_start: datetime,
    window_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Окна длиной duration, которые начинаются сразу после занятых интервалов.

    Окно ни с чем не пересекается и целиком лежит в [window_start, window_end].
    Не больше MAX_FREE_SLOTS, по времени.
    """
    slots: list[tuple[datetime, datetime]] = []
    for start in sorted({busy_end for _, busy_end in busy}):
        end = start + duration
        if start < window_start or end > window_end:
            continue
        if any(busy_start < end and busy_end > start for busy_start, busy_end in busy):
            continue
        slots.append((start, end))
        if len(slots) == MAX_FREE_SLOTS:
            break
    return slots


def _check_event_end(args: CreateEventArgs) -> None:
    if args.ends_at is None:
        return
    if not isinstance(args.starts_at, datetime):
        # Встреча на весь день хранится без конца (план S1, решение 10).
        raise InvalidArgumentsError(
            "У встречи на весь день нет времени конца", {"invalid": ["ends_at"]}
        )
    if args.ends_at <= args.starts_at:
        raise InvalidArgumentsError("Конец встречи раньше начала", {"invalid": ["ends_at"]})


def _new_event(args: CreateEventArgs, user: UserRow, embedding: list[float] | None) -> NewItem:
    """Встреча с временем по умолчанию из настроек. Время в прошлом → PastTimeError."""
    tz = args.caller.tz
    if isinstance(args.starts_at, datetime):
        _reject_past("starts_at", args.starts_at, tz)
        starts_at = to_utc(args.starts_at)
        ends_at: datetime | None = (
            to_utc(args.ends_at)
            if args.ends_at is not None
            else starts_at + timedelta(minutes=user.default_event_min)
        )
        all_day = False
        # Без явного remind_before_min напоминаем за столько минут, сколько в настройках.
        remind_before = args.remind_before_min
        if remind_before is None:
            remind_before = user.remind_before_min
    else:
        _reject_past_day(args.starts_at, tz)
        starts_at = local_midnight_utc(args.starts_at, tz)
        ends_at = None
        all_day = True
        # У встречи на весь день нет автонапоминания, только явное (план S1, решение 10).
        remind_before = args.remind_before_min
    return NewItem(
        kind="event",
        title=args.title,
        notes=args.notes,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        remind_at=_reminder(starts_at, remind_before),
        rrule=args.rrule,
        participants=args.participants,
        source_text=args.source_text,
        idempotency_key=args.caller.idempotency_key,
        embedding=embedding,
    )


def _reminder(starts_at: datetime, remind_before_min: int | None) -> datetime | None:
    """Момент напоминания или None: 0 — без напоминания; уже прошедшее — тоже без (решение 11)."""
    if not remind_before_min:
        return None
    remind_at = starts_at - timedelta(minutes=remind_before_min)
    return remind_at if remind_at > now_utc() else None


async def _conflict(
    items: ItemRepo, caller: Caller, starts_at: datetime, ends_at: datetime
) -> MutationResult | None:
    """Ответ «конфликт» со свободными окнами или None, если время свободно."""
    conflicts = await items.find_events_overlapping(caller.user_id, starts_at, ends_at)
    if not conflicts:
        return None
    day = to_user_tz(starts_at, caller.tz).date()
    window_start = max(local_time_utc(day, FREE_SLOTS_FROM, caller.tz), now_utc())
    window_end = local_time_utc(day, FREE_SLOTS_TO, caller.tz)
    busy_rows = await items.find_events_overlapping(caller.user_id, window_start, window_end)
    busy = [(row.starts_at, row.ends_at) for row in busy_rows]
    slots = free_slots(busy, ends_at - starts_at, window_start, window_end)
    return MutationResult(
        status="conflict",
        item=None,
        conflicts=[_to_brief(row, caller.tz) for row in conflicts],
        free_slots=[
            TimeSlot(starts_at=to_user_tz(start, caller.tz), ends_at=to_user_tz(end, caller.tz))
            for start, end in slots
        ],
    )


async def require_user(session: AsyncSession, user_id: int) -> UserRow:
    user = await UserRepo(session).get(user_id)
    if user is None:
        raise NotFoundError("Пользователь не найден")
    return user


async def _replay(items: ItemRepo, caller: Caller) -> ItemRow | None:
    """Запись, уже созданная этим вызовом раньше (тот же idempotency_key), или None.

    Повтор отвечает первой записью, даже если её время с тех пор прошло: ответ на первый
    вызов мог потеряться по таймауту.
    """
    if caller.idempotency_key is None:
        return None
    return await items.find_by_idempotency_key(caller.user_id, caller.idempotency_key)


async def _insert(
    session: AsyncSession, items: ItemRepo, caller: Caller, item: NewItem
) -> tuple[ItemRow, bool]:
    """Запись и её уведомление в текущей транзакции; второе значение — создана ли запись сейчас."""
    inserted = await items.insert_if_absent(caller.user_id, item)
    if inserted is None:
        # Тот же вызов параллельно уже создал запись: ON CONFLICT дождался его коммита.
        replay = await _replay(items, caller)
        if replay is None:
            raise RuntimeError("Запись не создана и не найдена после конфликта ключа")
        return replay, False
    if inserted.remind_at is not None:
        NotificationRepo(session).add(caller.user_id, inserted.id, inserted.remind_at)
    return inserted, True


def _reject_past(field: str, value: datetime | None, tz: str) -> None:
    if value is not None and value < now_utc() - PAST_TIME_TOLERANCE:
        raise PastTimeError("Время уже прошло", {field: to_user_tz(value, tz).isoformat()})


def _reject_past_day(day: date, tz: str) -> None:
    # Встреча на весь день сегодня — не в прошлом, хотя её полночь уже прошла.
    if day < local_today(tz):
        raise PastTimeError("Время уже прошло", {"starts_at": day.isoformat()})


def _start(value: date | datetime | None, tz: str) -> tuple[datetime | None, bool]:
    """Начало в UTC и признак «весь день»: дата без времени — это полночь в поясе пользователя."""
    if value is None:
        return None, False
    # datetime — подкласс date, поэтому проверяется первым.
    if isinstance(value, datetime):
        return to_utc(value), False
    return local_midnight_utc(value, tz), True


def _local(value: datetime | None, tz: str) -> datetime | None:
    return None if value is None else to_user_tz(value, tz)


def _to_brief(row: ItemRow, tz: str) -> ItemBrief:
    return ItemBrief(
        id=row.id,
        kind=row.kind,
        title=row.title,
        starts_at=_local(row.starts_at, tz),
        all_day=row.all_day,
        status=row.status,
    )


def to_item(row: ItemRow, tz: str) -> Item:
    return Item(
        id=row.id,
        kind=row.kind,
        title=row.title,
        notes=row.notes,
        starts_at=_local(row.starts_at, tz),
        ends_at=_local(row.ends_at, tz),
        all_day=row.all_day,
        remind_at=_local(row.remind_at, tz),
        status=row.status,
        rrule=row.rrule,
        participants=row.participants,
        source_text=row.source_text,
        created_at=to_user_tz(row.created_at, tz),
        updated_at=to_user_tz(row.updated_at, tz),
    )
