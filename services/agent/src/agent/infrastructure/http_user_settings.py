"""SettingsWriter по J8: PATCH /internal/v1/users/{user_id}/settings в mcp-контейнере (S3, шаг 5).

Тело — только меняемые поля, ответ D — настройки целиком (без user_id): пользователя хода
собираем из текущего и новых настроек. После успеха сбрасываем кеш
va:v1:agent:user:{max_user_id}: следующий ход возьмёт новый пояс из J8. Ключ идемпотентности —
user-settings:<trace_id> (повтор задаёт то же значение), 1 повтор у клиента. Сбой сброса
кеша — warning: старый пояс доживёт до TTL (1 ч).
"""

from dataclasses import replace

from pydantic import ValidationError

from agent.application.ports import SettingsChange
from agent.domain.user import UserContext
from agent.infrastructure.http_user_resolver import userCacheKey
from common.errors import AppError, ErrorCode
from common.http import ServiceHttpClient
from common.logging import getLogger
from common.redis import KeyedRedis
from common.trace import currentTraceId, newTraceId
from contracts.users import UpdateUserSettingsRequest, UserSettingsResponse, settingsPath

_log = getLogger(__name__)


# Примечание: http — тот же клиент к корню mcp-контейнера, что у resolve (5 с, 1 повтор)
class HttpSettingsWriter:
    # Примечание: cache — Redis агента, где лежит кеш пользователя
    def __init__(self, http: ServiceHttpClient, cache: KeyedRedis) -> None:
        self._http = http
        self._cache = cache

    # Примечание: PATCH → ответ D → сброс кеша → пользователь хода с новыми настройками
    async def update(self, user: UserContext, change: SettingsChange) -> UserContext:
        body = UpdateUserSettingsRequest(tz=change.tz)
        traceId = currentTraceId() or newTraceId()
        response = await self._http.request(
            "PATCH",
            settingsPath(user.userId),
            json=body.toPatch(),
            idempotencyKey=f"user-settings:{traceId}",
        )
        try:
            saved = UserSettingsResponse.fromJson(response.content)
        except ValidationError as exc:
            raise AppError(ErrorCode.INTERNAL, "J8 settings: ответ не по контракту") from exc
        await self._dropCache(user.maxUserId)
        return replace(user, tz=saved.tz, defaultEventMin=saved.defaultEventMin)

    # Примечание: кеш пользователя — не источник правды; не удалось удалить — только warning
    async def _dropCache(self, maxUserId: int) -> None:
        try:
            await self._cache.delete(userCacheKey(maxUserId))
        except Exception:  # noqa: BLE001
            _log.warning("user_cache_drop_failed")
