"""UserResolver по J8: POST /internal/v1/users/resolve в mcp-контейнере, кеш в Redis на 1 ч.

Кешируется только то, что не зависит от апдейта: user_id, пояс, настройки. Чат берётся из запроса.
Redis — не источник правды (правило 4): сбой кеша не мешает ходу, идём в J8.
"""

from typing import Final

from pydantic import ValidationError

from agent.domain.user import UserContext
from common.errors import AppError, ErrorCode
from common.http import ServiceHttpClient
from common.logging import getLogger
from common.redis import KeyedRedis, RedisKey
from contracts.users import RESOLVE_PATH, ResolveUserRequest, ResolveUserResponse

# docs/redis-keys.md: va:v1:agent:user:{max_user_id} — 1 ч
USER_CACHE_TTL_S: Final = 60 * 60

_log = getLogger(__name__)


# Примечание: пользователь из кеша или из J8; при первом обращении D создаёт его (created=true)
class HttpUserResolver:
    # Примечание: http — клиент к корню mcp-контейнера, создан в lifespan (правило 9)
    def __init__(self, http: ServiceHttpClient, cache: KeyedRedis) -> None:
        self._http = http
        self._cache = cache

    # Примечание: кеш → J8 → кеш; ошибки J8 уходят AppError с кодом стандарта
    async def resolve(self, maxUserId: int, maxChatId: int, name: str | None) -> UserContext:
        key = userCacheKey(maxUserId)
        cached = await self._readCache(key)
        if cached is not None:
            return toUserContext(cached, maxUserId, maxChatId)
        request = ResolveUserRequest(maxUserId=maxUserId, maxChatId=maxChatId, name=name)
        # Вызов идемпотентен по max_user_id — ключ от него разрешает один повтор (правило 6)
        response = await self._http.post(
            RESOLVE_PATH, json=request.toWire(), idempotencyKey=f"user-resolve:{maxUserId}"
        )
        try:
            resolved = ResolveUserResponse.fromJson(response.content)
        except ValidationError as exc:
            raise AppError(ErrorCode.INTERNAL, "J8 resolve: ответ не по контракту") from exc
        _log.info("user_resolved", user_id=resolved.userId, created=resolved.created)
        await self._writeCache(key, resolved)
        return toUserContext(resolved, maxUserId, maxChatId)

    # Примечание: битый JSON или недоступный Redis — промах кеша, а не ошибка хода
    async def _readCache(self, key: RedisKey) -> ResolveUserResponse | None:
        try:
            raw = await self._cache.getValue(key)
        except Exception:  # noqa: BLE001 — кеш можно потерять, ход продолжается
            _log.warning("user_cache_read_failed")
            return None
        if raw is None:
            return None
        try:
            return ResolveUserResponse.fromJson(raw)
        except ValidationError:
            _log.warning("user_cache_corrupted")
            return None

    # Примечание: created в кеш не пишем — это факт одного ответа, а не свойство пользователя
    async def _writeCache(self, key: RedisKey, resolved: ResolveUserResponse) -> None:
        payload = resolved.model_copy(update={"created": False}).toJson()
        try:
            await self._cache.setValue(key, payload, ttlS=USER_CACHE_TTL_S)
        except Exception:  # noqa: BLE001
            _log.warning("user_cache_write_failed")


# Примечание: ключ кеша по max_user_id
def userCacheKey(maxUserId: int) -> RedisKey:
    return RedisKey(service="agent", entity="user", id=str(maxUserId))


# Примечание: ответ J8 + чат из апдейта → пользователь хода; settings.voice_replies не читаем —
# голосового ответа ассистента нет (решение команды, S3)
def toUserContext(resolved: ResolveUserResponse, maxUserId: int, maxChatId: int) -> UserContext:
    return UserContext(
        userId=resolved.userId,
        maxUserId=maxUserId,
        maxChatId=maxChatId,
        tz=resolved.tz,
        defaultEventMin=resolved.settings.defaultEventMin,
    )
