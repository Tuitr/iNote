import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.tools import CreateEventArgs, CreateTaskArgs, SearchItemsArgs
from libs.core import embedding
from libs.core.db.models import ItemRow
from libs.core.embedding import EMBED_DIM, EmbedKind
from libs.core.services.items import ItemService
from libs.core.services.search import SearchService
from libs.core.services.users import UserService
from libs.core.tests.fakes import FakeEmbedder, FakeRedis

pytestmark = pytest.mark.anyio


def _caller(user_id: int) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "tz": "Europe/Moscow",
        "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
    }


async def _task(service: ItemService, user_id: int, title: str) -> None:
    await service.create_task(
        CreateTaskArgs.model_validate({"caller": _caller(user_id), "title": title})
    )


async def _search(service: SearchService, user_id: int, query: str) -> list[str]:
    result = await service.search(
        SearchItemsArgs.model_validate({"caller": _caller(user_id), "query": query})
    )
    return [hit.item.title for hit in result.results]


async def _embeddings(session_factory: async_sessionmaker[AsyncSession]) -> list[Any]:
    async with session_factory() as session:
        return list(await session.scalars(select(ItemRow.embedding).order_by(ItemRow.title)))


async def test_vector_is_saved_on_create(
    item_service: ItemService, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _task(item_service, user_id, "Позвонить стоматологу")
    await item_service.create_event(
        CreateEventArgs.model_validate(
            {
                "caller": _caller(user_id),
                "title": "Планёрка",
                "starts_at": "2099-01-05",
                "participants": ["Пётр"],
            }
        )
    )

    vectors = await _embeddings(session_factory)

    assert [len(vector) for vector in vectors] == [EMBED_DIM, EMBED_DIM]


async def test_other_words_with_same_meaning_are_found(
    item_service: ItemService, search_service: SearchService, user_id: int
) -> None:
    await _task(item_service, user_id, "Позвонить стоматологу")
    await _task(item_service, user_id, "Купить молоко")

    # Ни одного общего слова и мало общих букв: найти может только вектор.
    assert await _search(search_service, user_id, "зубной врач") == ["Позвонить стоматологу"]


async def test_deleted_and_foreign_records_are_not_found_by_meaning(
    item_service: ItemService,
    search_service: SearchService,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    other = await UserService(session_factory, "Europe/Moscow").resolve(2, None, None)
    await _task(item_service, other.user_id, "Позвонить стоматологу")
    await _task(item_service, user_id, "Записаться к стоматологу")
    async with session_factory() as session, session.begin():
        await session.execute(
            update(ItemRow).where(ItemRow.user_id == user_id).values(deleted_at=datetime.now(UTC))
        )

    assert await _search(search_service, user_id, "зубной врач") == []


async def test_create_works_when_embedder_is_down(
    item_service: ItemService,
    fake_embedder: FakeEmbedder,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_embedder.available = False

    with caplog.at_level(logging.WARNING):
        await _task(item_service, user_id, "Позвонить стоматологу")

    assert await _embeddings(session_factory) == [None]
    assert "Вектор не посчитан (document): EmbedderUnavailableError" in caplog.text


async def test_create_does_not_wait_for_a_hung_embedder(
    session_factory: async_sessionmaker[AsyncSession],
    fake_redis: FakeRedis,
    user_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HungEmbedder:
        async def embed(
            self, texts: list[str], kind: EmbedKind, timeout_s: float
        ) -> list[list[float]]:
            await asyncio.sleep(60)
            return []

    monkeypatch.setattr(embedding, "EMBED_TIMEOUT_SECONDS", 0.05)
    service = ItemService(session_factory, fake_redis, HungEmbedder())

    await _task(service, user_id, "Позвонить стоматологу")

    assert await _embeddings(session_factory) == [None]


async def test_search_falls_back_to_words_without_embedder(
    item_service: ItemService,
    fake_embedder: FakeEmbedder,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _task(item_service, user_id, "Позвонить стоматологу")
    fake_embedder.available = False
    down = SearchService(session_factory, fake_embedder)
    missing = SearchService(session_factory, None)

    assert await _search(down, user_id, "стоматолог") == ["Позвонить стоматологу"]
    assert await _search(missing, user_id, "стоматолог") == ["Позвонить стоматологу"]
    assert await _search(missing, user_id, "зубной врач") == []
