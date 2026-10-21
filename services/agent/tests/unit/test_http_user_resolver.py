import json
from typing import Final

import fakeredis
import pytest
import structlog
from pydantic import SecretStr

from agent.domain.user import UserContext
from agent.infrastructure.http_user_resolver import USER_CACHE_TTL_S, HttpUserResolver
from common.errors import AppError
from common.http import RetryPolicy, ServiceHttpClient
from common.redis import KeyedRedis
from common.trace import traceContext
from services.agent.tests.conftest import SERVICE_TOKEN
from services.agent.tests.fakes import FakeMcpHttp

TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
MAX_USER: Final = 100200300
CHAT: Final = 500600700
CACHE_KEY: Final = f"va:v1:agent:user:{MAX_USER}"


# Примечание: резолвер над фейковым J8 и fakeredis
class Rig:
    # Примечание: mcp — фейковый служебный REST; redis — fakeredis (connected=False — «лежит»)
    def __init__(self, mcp: FakeMcpHttp | None = None, *, redisUp: bool = True) -> None:
        self.mcp = mcp or FakeMcpHttp()
        server = fakeredis.FakeServer()
        server.connected = redisUp
        self.redis = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
        self.http = ServiceHttpClient(
            baseUrl="http://mcp:8000",
            timeoutS=1.0,
            serviceToken=SecretStr(SERVICE_TOKEN),
            retry=RetryPolicy(retries=1, initialDelayS=0, maxDelayS=0),
            upstreamName="mcp",
            transport=self.mcp.transport,
        )
        self.resolver = HttpUserResolver(self.http, KeyedRedis(self.redis))

    # Примечание: вызов внутри trace-контекста хода
    async def resolve(self, maxChatId: int = CHAT) -> UserContext:
        with traceContext(TRACE):
            return await self.resolver.resolve(MAX_USER, maxChatId, "Иван")


# Примечание: сырое значение ключа кеша; его отсутствие — провал теста
async def cachedRaw(rig: Rig) -> str:
    raw = await rig.redis.get(CACHE_KEY)
    assert isinstance(raw, str)
    return raw


# Примечание: первый вызов идёт в J8 с токеном, trace и ключом; ответ → пользователь хода
async def test_resolveCallsJ8() -> None:
    rig = Rig(FakeMcpHttp(userId=17, tz="Asia/Yekaterinburg"))
    user = await rig.resolve()
    assert user == UserContext(
        userId=17, maxUserId=MAX_USER, maxChatId=CHAT, tz="Asia/Yekaterinburg"
    )
    [request] = rig.mcp.resolves
    assert request.method == "POST"
    assert json.loads(request.content) == {
        "max_user_id": MAX_USER,
        "max_chat_id": CHAT,
        "name": "Иван",
    }
    assert request.headers["Authorization"] == f"Bearer {SERVICE_TOKEN}"
    assert request.headers["X-Trace-Id"] == TRACE
    assert request.headers["Idempotency-Key"] == f"user-resolve:{MAX_USER}"


# Примечание: второй вызов — из кеша (1 ч), без J8; чат берётся из запроса, а не из кеша
async def test_cacheHit() -> None:
    rig = Rig()
    await rig.resolve()
    cached = json.loads(await cachedRaw(rig))
    assert cached["user_id"] == 17
    assert cached["created"] is False
    assert USER_CACHE_TTL_S - 2 < await rig.redis.ttl(CACHE_KEY) <= USER_CACHE_TTL_S
    user = await rig.resolve(maxChatId=42)
    assert len(rig.mcp.resolves) == 1
    assert (user.userId, user.maxChatId) == (17, 42)


# Примечание: J8 упал один раз — повтор с тем же ключом проходит
async def test_retryOnce() -> None:
    rig = Rig(FakeMcpHttp(failures=1))
    user = await rig.resolve()
    assert user.userId == 17
    assert len(rig.mcp.resolves) == 2
    assert {r.headers["Idempotency-Key"] for r in rig.mcp.resolves} == {f"user-resolve:{MAX_USER}"}


# Примечание: J8 недоступен — AppError upstream_unavailable, в кеш ничего не пишется
async def test_unavailable() -> None:
    rig = Rig(FakeMcpHttp(down=True))
    with pytest.raises(AppError) as caught:
        await rig.resolve()
    assert caught.value.code == "upstream_unavailable"
    assert len(rig.mcp.resolves) == 2
    assert await rig.redis.exists(CACHE_KEY) == 0


# Примечание: ответ не по контракту (нет пояса) — internal, не кешируется
async def test_invalidResponse() -> None:
    rig = Rig(FakeMcpHttp(body={"user_id": 17}))
    with pytest.raises(AppError) as caught:
        await rig.resolve()
    assert caught.value.code == "internal"
    assert await rig.redis.exists(CACHE_KEY) == 0


# Примечание: битый кеш — промах: идём в J8 и перезаписываем ключ
async def test_corruptedCache() -> None:
    rig = Rig()
    await rig.redis.set(CACHE_KEY, "{не json", ex=60)
    with structlog.testing.capture_logs() as logs:
        user = await rig.resolve()
    assert user.userId == 17
    assert len(rig.mcp.resolves) == 1
    assert json.loads(await cachedRaw(rig))["user_id"] == 17
    assert any(e["event"] == "user_cache_corrupted" for e in logs)


# Примечание: Redis лежит — кеш пропускаем, пользователь всё равно определяется через J8
async def test_redisDown() -> None:
    rig = Rig(redisUp=False)
    with structlog.testing.capture_logs() as logs:
        user = await rig.resolve()
    assert user.userId == 17
    events = {e["event"] for e in logs}
    assert {"user_cache_read_failed", "user_cache_write_failed"} <= events


# Примечание: в логах только внутренний user_id — без имени и max_user_id (правило 10)
async def test_logsWithoutPersonalData() -> None:
    rig = Rig()
    with structlog.testing.capture_logs() as logs:
        await rig.resolve()
    [resolved] = [e for e in logs if e["event"] == "user_resolved"]
    assert (resolved["user_id"], resolved["created"]) == (17, True)
    assert "Иван" not in str(logs)
    assert str(MAX_USER) not in str(logs)
