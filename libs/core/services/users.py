"""Пользователи: найти или создать по max_user_id, прочитать и изменить настройки."""

from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.settings import UserSettings
from libs.core.db.models import UserRow
from libs.core.errors import NotFoundError
from libs.core.repositories.users import UserRepo
from libs.core.services.versioning import bump_version


@dataclass(frozen=True)
class ResolvedUser:
    user_id: int
    settings: UserSettings
    created: bool


@dataclass(frozen=True)
class UserProfile:
    user_id: int
    name: str | None
    settings: UserSettings


class UserService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], default_tz: str, redis: Redis
    ) -> None:
        self._session_factory = session_factory
        self._default_tz = default_tz
        self._redis = redis

    async def resolve(
        self, max_user_id: int, max_chat_id: int | None, name: str | None
    ) -> ResolvedUser:
        async with self._session_factory() as session, session.begin():
            repo = UserRepo(session)
            existing = await repo.get_by_max_user_id(max_user_id)
            if existing is None:
                inserted = await repo.insert_if_absent(
                    max_user_id, max_chat_id, name, self._default_tz
                )
                if inserted is not None:
                    return _resolved(inserted, created=True)
                # Того же пользователя одновременно создал параллельный запрос (бот повторил
                # апдейт). ON CONFLICT дождался его коммита, поэтому строка уже видна.
                existing = await repo.get_by_max_user_id(max_user_id)
                if existing is None:
                    raise RuntimeError("Пользователь не создан и не найден после конфликта вставки")
            # Scheduler шлёт напоминания в max_chat_id (J4): он должен быть свежим.
            # Строка из этой же сессии: изменённые поля SQLAlchemy запишет при коммите.
            if max_chat_id is not None:
                existing.max_chat_id = max_chat_id
            if name is not None:
                existing.name = name
            return _resolved(existing, created=False)

    async def find_profile(self, max_user_id: int) -> UserProfile | None:
        """Профиль по max_user_id из токена мини-приложения; None — такого пользователя нет."""
        async with self._session_factory() as session:
            user = await UserRepo(session).get_by_max_user_id(max_user_id)
        if user is None:
            return None
        return UserProfile(user_id=user.id, name=user.name, settings=_settings(user))

    async def update_settings(self, user_id: int, changes: dict[str, Any]) -> UserSettings:
        """Меняет переданные поля настроек. Версия данных растёт: от пояса зависят все даты."""
        async with self._session_factory() as session, session.begin():
            user = await UserRepo(session).update_settings(user_id, changes)
            if user is None:
                raise NotFoundError("Пользователь не найден")
        if changes:
            await bump_version(self._redis, user_id)
        return _settings(user)


def _settings(user: UserRow) -> UserSettings:
    return UserSettings(
        tz=user.tz,
        voice_replies=user.voice_replies,
        default_event_min=user.default_event_min,
        remind_before_min=user.remind_before_min,
    )


def _resolved(user: UserRow, created: bool) -> ResolvedUser:
    return ResolvedUser(user_id=user.id, settings=_settings(user), created=created)
