import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Final

import fakeredis
import pytest_asyncio

from agent.application.ports import DialogContext
from agent.infrastructure.redis_context import RedisDialogContextStore
from agent.infrastructure.redis_lock import RedisUserLock
from common.redis import KeyedRedis
from contracts.agent_side import ItemBrief, Turn
from services.agent.tests.fakes import ITEM

AT: Final = datetime(2026, 10, 21, 21, 30, tzinfo=UTC)


# Примечание: KeyedRedis поверх fakeredis
@pytest_asyncio.fixture
async def store() -> AsyncIterator[KeyedRedis]:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    yield KeyedRedis(client)
    await client.aclose()


# Примечание: лок: второй не получает, пока первый держит; после снятия — получает
async def test_lockAcquireRelease(store: KeyedRedis) -> None:
    lock = RedisUserLock(store)
    token = await lock.acquire(17, waitS=0)
    assert token is not None
    assert await lock.acquire(17, waitS=0.05) is None
    assert await lock.acquire(18, waitS=0) is not None
    assert await lock.release(17, token) is True
    assert await lock.acquire(17, waitS=0) is not None


# Примечание: чужой токен лок не снимает
async def test_lockForeignTokenDoesNotRelease(store: KeyedRedis) -> None:
    lock = RedisUserLock(store)
    token = await lock.acquire(17, waitS=0)
    assert token is not None
    assert await lock.release(17, "чужой") is False
    assert await lock.acquire(17, waitS=0) is None


# Примечание: контекст: пусто → сохранили → прочитали то же самое; pending=None удаляет ключ
async def test_contextRoundTrip(store: KeyedRedis) -> None:
    contexts = RedisDialogContextStore(store)
    assert await contexts.load(17) == DialogContext()
    saved = DialogContext(
        history=(Turn(role="user", text="привет", at=AT),),
        focus=(ItemBrief.fromWire(ITEM),),
        pending={"opaque": [1, 2, {"x": None}]},
        options=("сегодня 19:00", "завтра 9:00"),
    )
    await contexts.save(17, saved)
    assert await contexts.load(17) == saved
    raw = await store.client.get("va:v1:agent:ctx:17")
    assert raw is not None
    assert json.loads(raw)[0]["text"] == "привет"
    await contexts.save(17, DialogContext(history=saved.history, focus=saved.focus))
    assert await store.client.exists("va:v1:agent:pending:17") == 0


# Примечание: pending Brain лежит как есть рядом с вариантами; старый формат — «нет вопроса»
async def test_pendingStoredOpaqueWithOptions(store: KeyedRedis) -> None:
    contexts = RedisDialogContextStore(store)
    await contexts.save(17, DialogContext(pending={"kind": "time", "n": 1}, options=("9:00",)))
    raw = await store.client.get("va:v1:agent:pending:17")
    assert raw is not None
    assert json.loads(raw) == {"brain": {"kind": "time", "n": 1}, "options": ["9:00"]}
    await store.client.set("va:v1:agent:pending:17", json.dumps({"step": 1}), ex=60)
    assert (await contexts.load(17)).pending is None


# Примечание: битый JSON в ключе не роняет ход — эта часть контекста просто пустая
async def test_corruptedKeyIgnored(store: KeyedRedis) -> None:
    await store.client.set("va:v1:agent:ctx:17", "{not json", ex=60)
    await store.client.set("va:v1:agent:focus:17", json.dumps([ITEM]), ex=60)
    context = await RedisDialogContextStore(store).load(17)
    assert context.history == ()
    assert len(context.focus) == 1
