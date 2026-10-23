"""История диалога: агент записывает каждый ход, мини-приложение читает (J5, S2)."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.api import DialogPage, DialogTurnView
from libs.contracts.internal import DialogTurnsRequest
from libs.core.errors import NotFoundError
from libs.core.repositories.dialog import DialogRepo
from libs.core.repositories.items import ItemRepo
from libs.core.repositories.users import UserRepo
from libs.core.services.items import to_brief
from libs.core.services.time import to_user_tz


class DialogService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record_turns(self, request: DialogTurnsRequest) -> None:
        async with self._session_factory() as session, session.begin():
            if await UserRepo(session).get(request.user_id) is None:
                raise NotFoundError("Пользователь не найден")
            await DialogRepo(session).add_turns(
                request.user_id, request.trace_id, request.turns, request.latency_ms
            )

    async def list_turns(self, user_id: int, tz: str, before: int | None, limit: int) -> DialogPage:
        """Страница истории от новых реплик к старым; next_cursor — id последней, None в конце."""
        async with self._session_factory() as session:
            # На одну реплику больше: так видно, есть ли следующая страница.
            rows = await DialogRepo(session).list_before(user_id, before, limit + 1)
            page = rows[:limit]
            item_ids = sorted({item_id for row in page for item_id in row.item_ids})
            items = {
                item.id: to_brief(item, tz)
                for item in await ItemRepo(session).find_by_ids(user_id, item_ids)
            }
        turns = [
            DialogTurnView(
                id=str(row.id),
                role=row.role,
                text=row.text,
                input_type=row.input_type,
                created_at=to_user_tz(row.created_at, tz),
                # Удалённые записи из истории пропадают: карточку уже не открыть.
                items=[items[item_id] for item_id in row.item_ids if item_id in items],
            )
            for row in page
        ]
        next_cursor = str(page[-1].id) if len(rows) > limit else None
        return DialogPage(turns=turns, next_cursor=next_cursor)
