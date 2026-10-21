"""Запросы к таблице users."""

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from libs.core.db.models import UserRow


class UserRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, user_id: int) -> UserRow | None:
        return await self._session.get(UserRow, user_id)

    async def get_by_max_user_id(self, max_user_id: int) -> UserRow | None:
        # Единственный поиск не по user_id: по max_user_id как раз и узнают user_id.
        return await self._session.scalar(select(UserRow).where(UserRow.max_user_id == max_user_id))

    async def insert_if_absent(
        self, max_user_id: int, max_chat_id: int | None, name: str | None, tz: str
    ) -> UserRow | None:
        """Новая строка или None, если пользователя с таким max_user_id уже создал другой запрос."""
        statement = (
            insert(UserRow)
            .values(max_user_id=max_user_id, max_chat_id=max_chat_id, name=name, tz=tz)
            .on_conflict_do_nothing(index_elements=[UserRow.max_user_id])
            .returning(UserRow)
        )
        return await self._session.scalar(statement)
