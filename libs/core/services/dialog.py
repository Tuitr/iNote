"""История диалога: агент записывает каждый ход, мини-приложение читает (J5, S2)."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.internal import DialogTurnsRequest
from libs.core.errors import NotFoundError
from libs.core.repositories.dialog import DialogRepo
from libs.core.repositories.users import UserRepo


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
