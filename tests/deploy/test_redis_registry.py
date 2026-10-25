"""Реестр Redis ↔ код (S4, шаг 3).

1. Шаблоны ключей, которые строит код бота и агента (`RedisKey(...)`), совпадают со строками
   зоны C в docs/redis-keys.md — ни лишних, ни пропущенных.
2. Каждый адаптер бота и агента пишет ключ с TTL ровно как в реестре (fakeredis); вместе они
   покрывают все строки зоны C.
3. Разбор реестра и аудит живых ключей (`common.testing.redis_registry`) работают на краях.
"""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import fakeredis
import httpx
import pytest
from pydantic import SecretStr

from agent.application.ports import DialogContext
from agent.infrastructure.http_user_resolver import HttpUserResolver
from agent.infrastructure.redis_context import RedisDialogContextStore
from agent.infrastructure.redis_llm_quota import RedisLlmQuota
from agent.infrastructure.redis_lock import RedisUserLock
from bot.application.deliver_notification import DEFAULT_CLAIM_TTL_S
from bot.application.ports import StoredAction
from bot.infrastructure.redis_adapters import (
    RedisCallbackStore,
    RedisMarkerStore,
    RedisNotificationLedger,
    RedisRateLimiter,
    RedisUpdateDeduplicator,
)
from common.http import ServiceHttpClient
from common.redis import KeyedRedis
from common.testing.redis_registry import (
    TTL_GONE,
    TTL_NONE,
    RegistryRow,
    auditKeys,
    loadRegistry,
    parseRegistry,
    parseTtl,
    rowFor,
)
from contracts.agent_side import Turn

ROOT: Final = Path(__file__).resolve().parents[2]
REGISTRY: Final = ROOT / "docs" / "redis-keys.md"
SOURCES: Final = (ROOT / "services" / "bot" / "src", ROOT / "services" / "agent" / "src")
# RedisKey("bot", "upd", …) или RedisKey(service="agent", entity="user", …)
_KEY_CALL_RE: Final = re.compile(
    r'RedisKey\(\s*(?:service=)?"(?P<service>[a-z_]+)",\s*(?:entity=)?"(?P<entity>[a-z_]+)"'
)
# агент строит ключи контекста через _key("ctx", user_id) → RedisKey("agent", entity, …)
_AGENT_KEY_RE: Final = re.compile(r'_key\("(?P<entity>[a-z_]+)"')


# Примечание: строки реестра зоны C (пишут бот или агент, уже в коде)
def ownRows() -> list[RegistryRow]:
    return [row for row in loadRegistry(REGISTRY) if row.ownedByC]


# Примечание: пары (сервис, сущность) всех ключей, которые строит код бота и агента
def keysInCode() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for root in SOURCES:
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            found |= {(m["service"], m["entity"]) for m in _KEY_CALL_RE.finditer(text)}
            if path.name == "redis_context.py":
                found |= {("agent", m["entity"]) for m in _AGENT_KEY_RE.finditer(text)}
    return found


# Примечание: шаблоны ключей в коде и в реестре совпадают — ни лишних, ни пропущенных
def test_registryMatchesCode() -> None:
    registry = {(row.service, row.entity) for row in ownRows()}
    code = keysInCode()
    assert code - registry == set(), "ключи в коде без строки реестра"
    assert registry - code == set(), "строки реестра, которых код не строит"


# Примечание: J8 resolve для кеша пользователя агента — без сети
def resolveTransport() -> httpx.MockTransport:
    body = {"user_id": 17, "tz": "Europe/Moscow", "settings": {}, "created": False}
    return httpx.MockTransport(lambda _: httpx.Response(200, json=body))


# Примечание: все адаптеры зоны C пишут по ключу; TTL каждого — как в реестре; покрыты все строки
async def test_adaptersUseRegistryTtl() -> None:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    store = KeyedRedis(client)
    await RedisUpdateDeduplicator(store).isFirstSeen("msg.registry")
    await RedisRateLimiter(store).check(1)
    await RedisMarkerStore(store).save(42)
    await RedisCallbackStore(store).save(StoredAction(action="undo", args={}, chatId=2))
    ledger = RedisNotificationLedger(store)
    token = await ledger.claim("5c1e7a90-3f2b-4d6c-8e1a-9b0c2d3e4f5a", DEFAULT_CLAIM_TTL_S)
    assert token is not None
    await ledger.markDelivered("5c1e7a90-3f2b-4d6c-8e1a-9b0c2d3e4f5a", "mid.1")
    assert await RedisUserLock(store).acquire(17, waitS=0.1) is not None
    turn = Turn(role="user", text="привет", at=datetime(2026, 10, 21, 10, tzinfo=UTC))
    context = DialogContext(history=(turn,), pending={"kind": "time"}, options=("9:00",))
    await RedisDialogContextStore(store).save(17, context)
    assert await RedisLlmQuota(store, limit=1).allow(17, datetime(2026, 10, 21, tzinfo=UTC).date())
    async with ServiceHttpClient(
        baseUrl="http://mcp:8000",
        timeoutS=5,
        serviceToken=SecretStr("t" * 32),
        upstreamName="mcp",
        transport=resolveTransport(),
    ) as http:
        await HttpUserResolver(http, store).resolve(100, 200, None)
    rows = ownRows()
    seen: set[tuple[str, str]] = set()
    async for key in client.scan_iter("va:v1:*"):
        row = rowFor(key, rows)
        assert row is not None, f"{key} нет в реестре"
        assert await client.ttl(key) == row.ttlS, key
        seen.add((row.service, row.entity))
    assert {(r.service, r.entity) for r in rows} - seen == set(), "строки реестра без проверки TTL"
    await client.aclose()


# Примечание: TTL реестра по-русски: секунды, минуты, часы, дни; «без TTL»; мусор — ошибка
@pytest.mark.parametrize(
    ("raw", "seconds"),
    [("60 с", 60), ("10 мин", 600), ("24 ч", 86400), ("1 день", 86400), ("7 дней", 604800)],
)
def test_parseTtl(raw: str, seconds: int) -> None:
    assert parseTtl(raw) == seconds


# Примечание: «без TTL» (с выделением) — None; непонятная единица — ошибка разбора
def test_parseTtlEdges() -> None:
    assert parseTtl("**без TTL**") is None
    with pytest.raises(ValueError, match="TTL"):
        parseTtl("навсегда")


# Примечание: аудит: ключ не из реестра, без TTL, с TTL больше реестра — проблема; белый список
# mcp:ver и исчезнувший ключ — нет
def test_auditKeys() -> None:
    rows = parseRegistry(REGISTRY.read_text(encoding="utf-8"))
    problems = auditKeys(
        [
            ("va:v1:bot:upd:msg.1", 100),
            ("va:v1:bot:unknown:1", 100),
            ("va:v1:agent:ctx:17", TTL_NONE),
            ("va:v1:agent:lock:17", 3600),
            ("va:v1:mcp:ver:17", TTL_NONE),
            ("va:v1:bot:cb:gone", TTL_GONE),
        ],
        rows,
    )
    assert problems == [
        "va:v1:bot:unknown:1: нет в docs/redis-keys.md",
        "va:v1:agent:ctx:17: без TTL",
        "va:v1:agent:lock:17: TTL 3600 с больше, чем в реестре (60 с)",
    ]
