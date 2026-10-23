"""Записи пользователя: создание, изменение, выполнение, удаление, восстановление, список.

Один метод — одна транзакция; версия данных в Redis растёт только после коммита (развилка 4.3-А).
"""

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.items import Item, ItemBrief
from libs.contracts.tools import (
    Caller,
    CheckConflictsArgs,
    CheckConflictsResult,
    CompleteItemArgs,
    CreateEventArgs,
    CreateTaskArgs,
    DateRange,
    DeleteItemArgs,
    ListItemsArgs,
    ListItemsResult,
    MutationResult,
    TimeSlot,
    UpdateItemArgs,
)
from libs.core.db.models import ItemRow, UserRow
from libs.core.errors import InvalidArgumentsError, NotFoundError, PastTimeError
from libs.core.repositories.items import ItemRepo, NewItem
from libs.core.repositories.notifications import NotificationRepo
from libs.core.repositories.users import UserRepo
from libs.core.services.recurrence import check_rrule, next_occurrence
from libs.core.services.time import (
    days_range_utc,
    local_midnight_utc,
    local_time_utc,
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
# Сколько записей с подходящим коротким id показываем агенту на выбор.
MAX_ID_CANDIDATES = 5


class ItemService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._session_factory = session_factory
        self._redis = redis
        # Часы подменяются в тестах: время в них заморожено.
        self._clock = clock

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
            search_hints=args.search_hints,
        )
        if task.rrule is not None:
            check_rrule(task.rrule, task.starts_at or task.remind_at, caller.tz)
        now = self._clock()
        created = False
        async with self._session_factory() as session, session.begin():
            await require_user(session, caller.user_id)
            items = ItemRepo(session)
            row = await _replay(items, caller)
            if row is None:
                _reject_past("remind_at", task.remind_at, caller.tz, now)
                row, created = await _insert(session, items, caller, task)
        if created:
            await bump_version(self._redis, caller.user_id)
        return MutationResult(status="created", item=to_item(row, caller.tz))

    async def create_event(self, args: CreateEventArgs) -> MutationResult:
        caller = args.caller
        _check_event_end(args)
        now = self._clock()
        created = False
        async with self._session_factory() as session, session.begin():
            user = await require_user(session, caller.user_id)
            items = ItemRepo(session)
            row = await _replay(items, caller)
            if row is None:
                event = _new_event(args, user, now)
                # ends_at нет только у встречи на весь день, а она не конфликтует (решение 10).
                if not args.allow_conflict and event.starts_at and event.ends_at:
                    conflict = await _conflict(
                        items, caller, event.starts_at, event.ends_at, None, now
                    )
                    if conflict is not None:
                        return conflict
                row, created = await _insert(session, items, caller, event)
        if created:
            await bump_version(self._redis, caller.user_id)
        return MutationResult(status="created", item=to_item(row, caller.tz))

    async def update_item(self, args: UpdateItemArgs) -> MutationResult:
        """Переданные поля заменяются, null очищает, непереданные не меняются (J3).

        Повтор того же вызова ничего не меняет, поэтому ключ идемпотентности здесь хранить
        не нужно.
        """
        caller = args.caller
        changed = args.model_fields_set - {"caller", "id", "allow_conflict"}
        now = self._clock()
        async with self._session_factory() as session, session.begin():
            user = await require_user(session, caller.user_id)
            items = ItemRepo(session)
            row = await _find_one(items, caller, args.id, deleted=False)
            values = {
                field: value
                for field, value in _updated_values(args, changed, row, user, now).items()
                # Двойное нажатие кнопки повторяет те же значения: запись не трогаем.
                if getattr(row, field) != value
            }
            starts_at = values.get("starts_at", row.starts_at)
            ends_at = values.get("ends_at", row.ends_at)
            moved = bool({"starts_at", "ends_at"} & values.keys())
            if row.kind == "event" and moved and not args.allow_conflict and starts_at and ends_at:
                conflict = await _conflict(items, caller, starts_at, ends_at, row.id, now)
                if conflict is not None:
                    return conflict
            if values:
                old_remind_at = row.remind_at
                row = await items.update(caller.user_id, row.id, values)
                if row.remind_at != old_remind_at:
                    await reschedule(session, row, now)
        if values:
            await bump_version(self._redis, caller.user_id)
        return MutationResult(status="updated", item=to_item(row, caller.tz))

    async def complete_item(self, args: CompleteItemArgs) -> MutationResult:
        """«Выполнено» (done=true) или снова в работу (done=false); повтор ничего не меняет."""
        caller = args.caller
        target = "done" if args.done else "open"
        now = self._clock()
        async with self._session_factory() as session, session.begin():
            await require_user(session, caller.user_id)
            items = ItemRepo(session)
            row = await _find_one(items, caller, args.id, deleted=False)
            changed = row.status != target
            if changed:
                values = {"status": target}
                if args.done:
                    # Выполнено вхождение серии: запись переезжает на следующее и остаётся open.
                    values = next_in_series(row, caller.tz, now) or values
                row = await items.update(caller.user_id, row.id, values)
                await reschedule(session, row, now)
        if changed:
            await bump_version(self._redis, caller.user_id)
        return MutationResult(
            status="completed" if args.done else "updated", item=to_item(row, caller.tz)
        )

    async def delete_item(self, args: DeleteItemArgs) -> MutationResult:
        """Мягкое удаление или его отмена (restore); повтор ничего не меняет.

        Ищем среди удалённых и неудалённых сразу: повторное «удали» по тому же id должно
        ответить «удалено», а не «не найдено».
        """
        caller = args.caller
        now = self._clock()
        async with self._session_factory() as session, session.begin():
            await require_user(session, caller.user_id)
            items = ItemRepo(session)
            row = await _find_one(items, caller, args.id, deleted=None)
            changed = (row.deleted_at is None) != args.restore
            if changed:
                deleted_at = None if args.restore else now
                row = await items.update(caller.user_id, row.id, {"deleted_at": deleted_at})
                await reschedule(session, row, now)
        if changed:
            await bump_version(self._redis, caller.user_id)
        # В J3 нет статуса «восстановлено»: восстановление — изменение записи.
        return MutationResult(
            status="updated" if args.restore else "deleted", item=to_item(row, caller.tz)
        )

    async def check_conflicts(self, args: CheckConflictsArgs) -> CheckConflictsResult:
        caller = args.caller
        if args.ends_at <= args.starts_at:
            raise InvalidArgumentsError("Конец раньше начала", {"invalid": ["ends_at"]})
        now = self._clock()
        async with self._session_factory() as session, session.begin():
            await require_user(session, caller.user_id)
            items = ItemRepo(session)
            exclude = None
            if args.exclude_id is not None:
                exclude = (await _find_one(items, caller, args.exclude_id, deleted=False)).id
            conflict = await _conflict(
                items, caller, to_utc(args.starts_at), to_utc(args.ends_at), exclude, now
            )
        if conflict is None:
            return CheckConflictsResult(conflicts=[], free_slots=[])
        return CheckConflictsResult(conflicts=conflict.conflicts, free_slots=conflict.free_slots)

    async def list_items(self, args: ListItemsArgs) -> ListItemsResult:
        caller = args.caller
        if args.to <= args.from_:
            # to не включается: период «с 22 по 22 октября» пуст, модель ошиблась с датами.
            raise InvalidArgumentsError(
                "Конец периода должен быть позже начала", {"invalid": ["to"]}
            )
        # На одну запись больше лимита: так видно, что записей больше, чем отдаём.
        items = await self.list_period(
            caller.user_id,
            caller.tz,
            args.from_,
            args.to,
            [kind.value for kind in args.kind] if args.kind else None,
            args.status,
            MAX_LIST_ITEMS + 1,
        )
        return ListItemsResult(
            items=items[:MAX_LIST_ITEMS],
            range=DateRange(from_=args.from_, to=args.to),
            truncated=len(items) > MAX_LIST_ITEMS,
        )

    async def list_period(
        self,
        user_id: int,
        tz: str,
        start: date,
        end: date,
        kinds: list[str] | None,
        status: str,
        limit: int,
    ) -> list[Item]:
        """Записи за даты [start, end) в поясе пользователя: «весь день» первыми, потом по времени.

        status: open, done или all (все, включая отменённые).
        """
        start_utc, end_utc = days_range_utc(start, end, tz)
        statuses = None if status == "all" else [status]
        async with self._session_factory() as session, session.begin():
            await require_user(session, user_id)
            rows = await ItemRepo(session).list_in_range(
                user_id, start_utc, end_utc, kinds, statuses, limit
            )
        return [to_item(row, tz) for row in rows]

    async def get_item(self, user_id: int, tz: str, item_id: UUID) -> Item:
        """Карточка записи. Удалённая, чужая и несуществующая — одинаково not_found (КиП)."""
        async with self._session_factory() as session:
            rows = await ItemRepo(session).find_by_ref(
                user_id, str(item_id), deleted=False, limit=1, for_update=False
            )
        if not rows:
            raise NotFoundError("Запись не найдена", {"id": str(item_id)})
        return to_item(rows[0], tz)


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


def _new_event(args: CreateEventArgs, user: UserRow, now: datetime) -> NewItem:
    """Встреча с временем по умолчанию из настроек. Время в прошлом → PastTimeError."""
    tz = args.caller.tz
    if isinstance(args.starts_at, datetime):
        _reject_past("starts_at", args.starts_at, tz, now)
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
        _reject_past_day(args.starts_at, tz, now)
        starts_at = local_midnight_utc(args.starts_at, tz)
        ends_at = None
        all_day = True
        # У встречи на весь день нет автонапоминания, только явное (план S1, решение 10).
        remind_before = args.remind_before_min
    if args.rrule is not None:
        check_rrule(args.rrule, starts_at, tz)
    return NewItem(
        kind="event",
        title=args.title,
        notes=args.notes,
        starts_at=starts_at,
        ends_at=ends_at,
        all_day=all_day,
        remind_at=_reminder(starts_at, remind_before, now),
        rrule=args.rrule,
        participants=args.participants,
        source_text=args.source_text,
        idempotency_key=args.caller.idempotency_key,
        search_hints=args.search_hints,
    )


def _updated_values(
    args: UpdateItemArgs, changed: set[str], row: ItemRow, user: UserRow, now: datetime
) -> dict[str, Any]:
    """Новые значения полей по аргументам update_item; проверяет их вместе с остальными полями."""
    tz = args.caller.tz
    values: dict[str, Any] = {}
    if "title" in changed:
        if args.title is None:
            raise InvalidArgumentsError("Название нельзя очистить", {"invalid": ["title"]})
        values["title"] = args.title
    for field in ("notes", "rrule", "source_text", "search_hints"):
        if field in changed:
            values[field] = getattr(args, field)
    if "participants" in changed:
        values["participants"] = args.participants or []
    if "kind" in changed:
        # Задачу можно сделать напоминанием и обратно; встреча задачей не становится.
        if args.kind is None or row.kind == "event":
            raise InvalidArgumentsError("Нельзя так поменять вид записи", {"invalid": ["kind"]})
        values["kind"] = args.kind

    starts_at, all_day = row.starts_at, row.all_day
    ends_at, remind_at = row.ends_at, row.remind_at
    if "starts_at" in changed:
        starts_at, all_day = _start(args.starts_at, tz)
        if isinstance(args.starts_at, datetime):
            _reject_past("starts_at", args.starts_at, tz, now)
        elif args.starts_at is not None:
            _reject_past_day(args.starts_at, tz, now)
        shift = starts_at - row.starts_at if starts_at and row.starts_at else None
        if row.kind == "event":
            ends_at = _moved_end(row, starts_at, all_day, shift, user)
        if shift is not None and remind_at is not None:
            # Напоминание едет вместе с началом: «за 15 минут» остаётся «за 15 минут».
            remind_at = remind_at + shift
    if "ends_at" in changed:
        if args.ends_at is not None and all_day:
            raise InvalidArgumentsError(
                "У встречи на весь день нет времени конца", {"invalid": ["ends_at"]}
            )
        ends_at = None if args.ends_at is None else to_utc(args.ends_at)
    if "remind_before_min" in changed:
        if starts_at is None:
            raise InvalidArgumentsError(
                "Не от чего отсчитать напоминание", {"missing": ["starts_at"]}
            )
        remind_at = _reminder(starts_at, args.remind_before_min, now)
    if "remind_at" in changed:
        _reject_past("remind_at", args.remind_at, tz, now)
        remind_at = None if args.remind_at is None else to_utc(args.remind_at)

    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise InvalidArgumentsError("Конец встречи раньше начала", {"invalid": ["ends_at"]})
    if values.get("kind", row.kind) == "reminder" and remind_at is None:
        raise InvalidArgumentsError("Для напоминания нужно время", {"missing": ["remind_at"]})
    if row.kind == "event" and starts_at is None:
        raise InvalidArgumentsError("У встречи должно быть начало", {"missing": ["starts_at"]})
    rule = values.get("rrule", row.rrule)
    if rule is not None and changed & {"rrule", "starts_at", "remind_at", "remind_before_min"}:
        check_rrule(rule, starts_at or remind_at, tz)
    for field, value in (
        ("starts_at", starts_at),
        ("all_day", all_day),
        ("ends_at", ends_at),
        ("remind_at", remind_at),
    ):
        if value != getattr(row, field):
            values[field] = value
    return values


def _moved_end(
    row: ItemRow,
    starts_at: datetime | None,
    all_day: bool,
    shift: timedelta | None,
    user: UserRow,
) -> datetime | None:
    """Конец перенесённой встречи: длительность сохраняется, у «весь день» конца нет."""
    if all_day or starts_at is None:
        return None
    if row.ends_at is not None and shift is not None and not row.all_day:
        return row.ends_at + shift
    return starts_at + timedelta(minutes=user.default_event_min)


def next_in_series(row: ItemRow, tz: str, now: datetime) -> dict[str, Any] | None:
    """Даты следующего вхождения серии; None — повтора нет или серия закончилась."""
    current = row.starts_at or row.remind_at
    if row.rrule is None or current is None:
        return None
    following = next_occurrence(row.rrule, current, tz, now)
    if following is None:
        return None
    # Длительность встречи и «за сколько напомнить» сохраняются.
    shift_of = following.starts_at - current
    values: dict[str, Any] = {"rrule": following.rrule}
    for field in ("starts_at", "ends_at", "remind_at"):
        value = getattr(row, field)
        if value is not None:
            values[field] = value + shift_of
    return values


def _reminder(starts_at: datetime, remind_before_min: int | None, now: datetime) -> datetime | None:
    """Момент напоминания или None: 0 — без напоминания; уже прошедшее — тоже без (решение 11)."""
    if not remind_before_min:
        return None
    remind_at = starts_at - timedelta(minutes=remind_before_min)
    return remind_at if remind_at > now else None


async def _conflict(
    items: ItemRepo,
    caller: Caller,
    starts_at: datetime,
    ends_at: datetime,
    exclude_id: UUID | None,
    now: datetime,
) -> MutationResult | None:
    """Ответ «конфликт» со свободными окнами или None, если время свободно."""
    conflicts = await items.find_events_overlapping(caller.user_id, starts_at, ends_at, exclude_id)
    if not conflicts:
        return None
    day = to_user_tz(starts_at, caller.tz).date()
    window_start = max(local_time_utc(day, FREE_SLOTS_FROM, caller.tz), now)
    window_end = local_time_utc(day, FREE_SLOTS_TO, caller.tz)
    busy_rows = await items.find_events_overlapping(
        caller.user_id, window_start, window_end, exclude_id
    )
    busy = [(row.starts_at, row.ends_at) for row in busy_rows]
    slots = free_slots(busy, ends_at - starts_at, window_start, window_end)
    return MutationResult(
        status="conflict",
        item=None,
        conflicts=[to_brief(row, caller.tz) for row in conflicts],
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


async def _find_one(items: ItemRepo, caller: Caller, ref: str, deleted: bool | None) -> ItemRow:
    """Запись по полному или короткому id. Чужая и несуществующая — одинаково not_found (КиП)."""
    rows = await items.find_by_ref(caller.user_id, ref, deleted, MAX_ID_CANDIDATES, for_update=True)
    if not rows:
        raise NotFoundError("Запись не найдена", {"id": ref})
    if len(rows) > 1:
        # Черновик D: validation_error со списком записей, чтобы агент показал кнопки выбора.
        candidates = [to_brief(row, caller.tz).model_dump(mode="json") for row in rows]
        raise InvalidArgumentsError(
            "Под короткий id подходят несколько записей",
            {"invalid": ["id"], "candidates": candidates},
        )
    return rows[0]


async def reschedule(session: AsyncSession, row: ItemRow, now: datetime) -> None:
    """Уведомления по текущему состоянию записи: неотправленные отменяются, новое ставится,
    только если запись открыта, не удалена и время напоминания впереди.
    """
    notifications = NotificationRepo(session)
    await notifications.cancel_pending(row.user_id, row.id)
    is_active = row.status == "open" and row.deleted_at is None
    if is_active and row.remind_at is not None and row.remind_at > now:
        await notifications.schedule(row.user_id, row.id, row.remind_at)


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
        await NotificationRepo(session).schedule(caller.user_id, inserted.id, inserted.remind_at)
    return inserted, True


def _reject_past(field: str, value: datetime | None, tz: str, now: datetime) -> None:
    if value is not None and value < now - PAST_TIME_TOLERANCE:
        raise PastTimeError("Время уже прошло", {field: to_user_tz(value, tz).isoformat()})


def _reject_past_day(day: date, tz: str, now: datetime) -> None:
    # Встреча на весь день сегодня — не в прошлом, хотя её полночь уже прошла.
    if day < to_user_tz(now, tz).date():
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


def to_brief(row: ItemRow, tz: str) -> ItemBrief:
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
