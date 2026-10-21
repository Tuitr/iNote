"""Пользователи: найти по max_user_id или создать с настройками по умолчанию."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.settings import UserSettings
from libs.core.db.models import UserRow
from libs.core.repositories.users import UserRepo


@dataclass(frozen=True)
class ResolvedUser:
    user_id: int
    settings: UserSettings
    created: bool


class UserService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], default_tz: str) -> None:
        self._session_factory = session_factory
        self._default_tz = default_tz

    async def resolve(
        self, max_user_id: int, max_chat_id: int | None, name: str | None
    ) -> ResolvedUser:
        async with self._session_factory() as session, session.begin():
            repo = UserRepo(session)
            existing = await repo.get_by_max_user_id(max_user_id)
            if existing is not None:
                return _resolved(existing, created=False)
            inserted = await repo.insert_if_absent(max_user_id, max_chat_id, name, self._default_tz)
            if inserted is not None:
                return _resolved(inserted, created=True)
            # Того же пользователя одновременно создал параллельный запрос (бот повторил апдейт).
            # ON CONFLICT дождался его коммита, поэтому строка уже видна.
            concurrent = await repo.get_by_max_user_id(max_user_id)
            if concurrent is None:
                raise RuntimeError("Пользователь не создан и не найден после конфликта вставки")
            return _resolved(concurrent, created=False)


def _resolved(user: UserRow, created: bool) -> ResolvedUser:
    settings = UserSettings(
        tz=user.tz,
        voice_replies=user.voice_replies,
        default_event_min=user.default_event_min,
        remind_before_min=user.remind_before_min,
    )
    return ResolvedUser(user_id=user.id, settings=settings, created=created)
