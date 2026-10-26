"""Запросы к таблице items: каждый метод принимает user_id первым и видит только его записи.

Исключение — lock_passed_series: Scheduler обходит серии всех пользователей.
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    ColumnElement,
    Text,
    and_,
    case,
    cast,
    exists,
    func,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import TSQUERY, insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.core.db.models import ItemRow, NotificationRow


@dataclass(frozen=True)
class SearchFilter:
    """Ограничения поиска; период — по времени записи, а у записи без времени — по дате создания."""

    kinds: Sequence[str] | None
    statuses: Sequence[str]
    start: datetime | None
    end: datetime | None


@dataclass(frozen=True)
class NewItem:
    """Поля новой записи. Время — aware, в БД ляжет в UTC."""

    kind: str
    title: str
    notes: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    all_day: bool
    remind_at: datetime | None
    rrule: str | None
    participants: list[str]
    source_text: str | None
    idempotency_key: str | None
    search_hints: str | None


class ItemRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_if_absent(self, user_id: int, item: NewItem) -> ItemRow | None:
        """Новая строка или None, если у пользователя уже есть запись с тем же idempotency_key.

        Без ключа (None) запись создаётся всегда: в unique-индексе NULL не равен NULL.
        """
        statement = (
            insert(ItemRow)
            .values(id=uuid4(), user_id=user_id, **asdict(item))
            .on_conflict_do_nothing(index_elements=[ItemRow.user_id, ItemRow.idempotency_key])
            .returning(ItemRow)
        )
        return await self._session.scalar(statement)

    async def find_by_idempotency_key(self, user_id: int, idempotency_key: str) -> ItemRow | None:
        return await self._session.scalar(
            select(ItemRow).where(
                ItemRow.user_id == user_id, ItemRow.idempotency_key == idempotency_key
            )
        )

    async def find_by_ref(
        self, user_id: int, ref: str, deleted: bool | None, limit: int, for_update: bool
    ) -> list[ItemRow]:
        """Записи по полному id или по его первым 8 символам (J3), не больше limit.

        deleted: False — только неудалённые, True — только удалённые, None — любые.
        for_update — запись будут менять: блокируем до конца транзакции, чтобы параллельное
        изменение (второй вызов агента, сдвиг серии Scheduler) не перетёрло это.
        """
        if len(ref) == len(str(UUID(int=0))):
            matches_ref = ItemRow.id == UUID(ref)
        else:
            matches_ref = cast(ItemRow.id, Text).startswith(ref, autoescape=True)
        statement = select(ItemRow).where(ItemRow.user_id == user_id, matches_ref)
        if deleted is not None:
            statement = statement.where(
                ItemRow.deleted_at.is_not(None) if deleted else ItemRow.deleted_at.is_(None)
            )
        statement = statement.order_by(ItemRow.id).limit(limit)
        if for_update:
            statement = statement.with_for_update()
        return list(await self._session.scalars(statement))

    async def update(self, user_id: int, item_id: UUID, values: dict[str, Any]) -> ItemRow:
        """Меняет поля записи пользователя и updated_at; возвращает запись после изменения."""
        statement = (
            update(ItemRow)
            .where(ItemRow.user_id == user_id, ItemRow.id == item_id)
            .values(**values, updated_at=func.now())
            .returning(ItemRow)
            # Запись уже загружена в сессию: без populate_existing вернулись бы старые значения.
            .execution_options(synchronize_session=False, populate_existing=True)
        )
        row = await self._session.scalar(statement)
        if row is None:
            raise LookupError("Запись исчезла внутри транзакции")
        return row

    async def find_by_ids(self, user_id: int, item_ids: list[UUID]) -> list[ItemRow]:
        """Неудалённые записи пользователя с этими id (ссылки на карточки в истории диалога)."""
        if not item_ids:
            return []
        statement = select(ItemRow).where(
            ItemRow.user_id == user_id, ItemRow.deleted_at.is_(None), ItemRow.id.in_(item_ids)
        )
        return list(await self._session.scalars(statement))

    async def find_events_overlapping(
        self, user_id: int, start: datetime, end: datetime, exclude_id: UUID | None
    ) -> list[ItemRow]:
        """Открытые встречи со временем, которые пересекают [start, end); по времени начала.

        Встреча, которая кончается ровно в start, не пересекается. Задачи и встречи на весь день
        в пересечениях не участвуют (план S1, решение 10). exclude_id — переносимая встреча:
        с самой собой она не конфликтует.
        """
        statement = (
            select(ItemRow)
            .where(
                ItemRow.user_id == user_id,
                ItemRow.deleted_at.is_(None),
                ItemRow.kind == "event",
                ItemRow.status == "open",
                ItemRow.all_day.is_(False),
                ItemRow.starts_at < end,
                ItemRow.ends_at > start,
            )
            .order_by(ItemRow.starts_at, ItemRow.id)
        )
        if exclude_id is not None:
            statement = statement.where(ItemRow.id != exclude_id)
        return list(await self._session.scalars(statement))

    async def list_in_range(
        self,
        user_id: int,
        start: datetime,
        end: datetime,
        kinds: Sequence[str] | None,
        statuses: Sequence[str] | None,
        limit: int,
    ) -> list[ItemRow]:
        """Неудалённые записи с моментом в [start, end), по возрастанию времени.

        Момент записи — starts_at, а у напоминания без срока — remind_at. Записи без времени
        в период не попадают. «Весь день» хранится как полночь, поэтому идёт первым в своём дне.
        """
        moment = func.coalesce(ItemRow.starts_at, ItemRow.remind_at)
        statement = (
            select(ItemRow)
            .where(
                ItemRow.user_id == user_id,
                ItemRow.deleted_at.is_(None),
                moment >= start,
                moment < end,
            )
            .order_by(moment, ItemRow.all_day.desc(), ItemRow.created_at, ItemRow.id)
            .limit(limit)
        )
        if kinds is not None:
            statement = statement.where(ItemRow.kind.in_(kinds))
        if statuses is not None:
            statement = statement.where(ItemRow.status.in_(statuses))
        return list(await self._session.scalars(statement))

    async def list_open_series(
        self, user_id: int, end: datetime, kinds: Sequence[str] | None, limit: int
    ) -> list[ItemRow]:
        """Открытые серии (с rrule), чьё текущее вхождение раньше end: их даты дойдут до периода."""
        moment = func.coalesce(ItemRow.starts_at, ItemRow.remind_at)
        statement = (
            select(ItemRow)
            .where(
                ItemRow.user_id == user_id,
                ItemRow.deleted_at.is_(None),
                ItemRow.status == "open",
                ItemRow.rrule.is_not(None),
                moment < end,
            )
            .order_by(moment, ItemRow.id)
            .limit(limit)
        )
        if kinds is not None:
            statement = statement.where(ItemRow.kind.in_(kinds))
        return list(await self._session.scalars(statement))

    async def search_fulltext(
        self, user_id: int, query: str, search: SearchFilter, limit: int
    ) -> list[ItemRow]:
        """Записи, где есть хотя бы одно слово запроса (с учётом окончаний), лучшие первыми."""
        # plainto_tsquery требует все слова сразу («звонок & иван»); заменяем «&» на «|», чтобы
        # подходила запись хотя бы с одним словом. Приведение ::tsquery не стеммит слова второй раз.
        words = cast(
            func.replace(cast(func.plainto_tsquery("russian", query), Text), "&", "|"), TSQUERY
        )
        statement = (
            select(ItemRow)
            .where(_search_scope(user_id, search), ItemRow.search_tsv.op("@@")(words))
            .order_by(func.ts_rank_cd(ItemRow.search_tsv, words).desc(), ItemRow.id)
            .limit(limit)
        )
        return list(await self._session.scalars(statement))

    async def search_trigram(
        self, user_id: int, query: str, search: SearchFilter, min_similarity: float, limit: int
    ) -> list[tuple[ItemRow, float]]:
        """Записи, похожие на запрос по буквам (опечатки, падежи имён), со сходством 0–1."""
        similarity = func.word_similarity(literal(query, Text), ItemRow.search_text)
        statement = (
            select(ItemRow, similarity)
            .where(_search_scope(user_id, search), similarity >= min_similarity)
            .order_by(similarity.desc(), ItemRow.id)
            .limit(limit)
        )
        return [(row, float(score)) for row, score in await self._session.execute(statement)]

    async def lock_passed_series(self, limit: int) -> list[ItemRow]:
        """Открытые серии, чьё текущее вхождение уже прошло, — для Scheduler, по всем пользователям.

        Вхождение прошло, когда закончилось: конец встречи, конец дня для «весь день», иначе
        начало или время напоминания. Серию с ещё не отправленным созревшим напоминанием не
        трогаем: сдвиг отменил бы его. SKIP LOCKED — запись сейчас меняет пользователь
        или другая реплика.
        """
        occurrence_end = func.coalesce(
            case((ItemRow.all_day, ItemRow.starts_at + timedelta(days=1))),
            ItemRow.ends_at,
            ItemRow.starts_at,
            ItemRow.remind_at,
        )
        unsent_due = exists().where(
            NotificationRow.item_id == ItemRow.id,
            NotificationRow.status.in_(("pending", "sending")),
            NotificationRow.fire_at <= func.now(),
            or_(
                NotificationRow.fire_at == ItemRow.remind_at,
                and_(ItemRow.kind == "event", NotificationRow.fire_at == ItemRow.starts_at),
            ),
        )
        statement = (
            select(ItemRow)
            .where(
                ItemRow.rrule.is_not(None),
                ItemRow.status == "open",
                ItemRow.deleted_at.is_(None),
                occurrence_end <= func.now(),
                ~unsent_due,
            )
            .order_by(occurrence_end)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list(await self._session.scalars(statement))


def _search_scope(user_id: int, search: SearchFilter) -> ColumnElement[bool]:
    moment = func.coalesce(ItemRow.starts_at, ItemRow.remind_at, ItemRow.created_at)
    conditions = [
        ItemRow.user_id == user_id,
        ItemRow.deleted_at.is_(None),
        ItemRow.status.in_(search.statuses),
    ]
    if search.kinds is not None:
        conditions.append(ItemRow.kind.in_(search.kinds))
    if search.start is not None:
        conditions.append(moment >= search.start)
    if search.end is not None:
        conditions.append(moment < search.end)
    return and_(*conditions)
