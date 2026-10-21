from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.tools import CreateEventArgs, CreateTaskArgs, SearchItemsArgs, SearchItemsResult
from libs.core.db.models import ItemRow
from libs.core.errors import NotFoundError
from libs.core.services.items import ItemService
from libs.core.services.search import RRF_K, SearchService, rrf_scores
from libs.core.services.time import local_today
from libs.core.services.users import UserService

TOMORROW = local_today("Europe/Moscow") + timedelta(days=1)


def _caller(user_id: int) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "tz": "Europe/Moscow",
        "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
    }


def _query(user_id: int, query: str, **fields: Any) -> SearchItemsArgs:
    return SearchItemsArgs.model_validate({"caller": _caller(user_id), "query": query, **fields})


async def _task(service: ItemService, user_id: int, title: str, **fields: Any) -> None:
    await service.create_task(
        CreateTaskArgs.model_validate({"caller": _caller(user_id), "title": title, **fields})
    )


async def _event(service: ItemService, user_id: int, title: str, **fields: Any) -> None:
    await service.create_event(
        CreateEventArgs.model_validate(
            {
                "caller": _caller(user_id),
                "title": title,
                "starts_at": TOMORROW.isoformat(),
                **fields,
            }
        )
    )


def _titles(result: SearchItemsResult) -> list[str]:
    return [hit.item.title for hit in result.results]


class TestRrfScores:
    def test_first_place_everywhere_is_one(self) -> None:
        assert rrf_scores([["a", "b"], ["a", "b"]])["a"] == pytest.approx(1.0)

    def test_found_by_both_sources_beats_found_by_one(self) -> None:
        scores = rrf_scores([["a", "b"], ["b", "c"]])

        assert scores["b"] > scores["a"] > scores["c"]
        assert scores["a"] == pytest.approx(0.5)
        assert scores["c"] == pytest.approx((RRF_K + 1) / (RRF_K + 2) / 2)


@pytest.mark.anyio
class TestSearch:
    async def test_find_what_i_wrote_about_ivan(
        self, item_service: ItemService, search_service: SearchService, user_id: int
    ) -> None:
        await _task(item_service, user_id, "Позвонить Ивану", starts_at=TOMORROW.isoformat())
        await _task(item_service, user_id, "Купить молоко")
        await _event(item_service, user_id, "Встреча с дизайнером")

        result = await search_service.search(_query(user_id, "Иван"))

        assert _titles(result) == ["Позвонить Ивану"]
        assert result.best_match_confident is True
        assert 0 < result.results[0].score <= 1
        starts_at = result.results[0].item.starts_at
        assert starts_at is not None
        assert starts_at.utcoffset() == timedelta(hours=3)

    async def test_two_similar_records_are_not_a_confident_match(
        self, item_service: ItemService, search_service: SearchService, user_id: int
    ) -> None:
        await _task(item_service, user_id, "Позвонить Ивану")
        await _task(item_service, user_id, "Написать Ивану")

        result = await search_service.search(_query(user_id, "Иван"))

        assert sorted(_titles(result)) == ["Написать Ивану", "Позвонить Ивану"]
        assert result.best_match_confident is False

    async def test_finds_other_word_forms_and_participants(
        self, item_service: ItemService, search_service: SearchService, user_id: int
    ) -> None:
        await _task(item_service, user_id, "Позвонить в банк")
        await _event(item_service, user_id, "Планёрка", participants=["Пётр Сидоров"])

        by_word_form = await search_service.search(_query(user_id, "позвоню"))
        by_participant = await search_service.search(_query(user_id, "Сидоров"))

        assert _titles(by_word_form) == ["Позвонить в банк"]
        assert _titles(by_participant) == ["Планёрка"]

    async def test_deleted_and_foreign_records_are_not_found(
        self,
        item_service: ItemService,
        search_service: SearchService,
        user_id: int,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        other = await UserService(session_factory, "Europe/Moscow").resolve(2, None, None)
        await _task(item_service, other.user_id, "Позвонить Ивану")
        await _task(item_service, user_id, "Удалённый звонок Ивану")
        async with session_factory() as session, session.begin():
            await session.execute(
                update(ItemRow)
                .where(ItemRow.user_id == user_id)
                .values(deleted_at=datetime.now(UTC))
            )

        result = await search_service.search(_query(user_id, "Иван"))

        assert result.results == []
        assert result.best_match_confident is False

    async def test_filters_done_kind_and_period(
        self,
        item_service: ItemService,
        search_service: SearchService,
        user_id: int,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        await _task(item_service, user_id, "Отчёт Ивану", starts_at=TOMORROW.isoformat())
        await _task(item_service, user_id, "Сделанный отчёт", starts_at=TOMORROW.isoformat())
        await _event(item_service, user_id, "Встреча с Иваном")
        async with session_factory() as session, session.begin():
            await session.execute(
                update(ItemRow).where(ItemRow.title == "Сделанный отчёт").values(status="done")
            )

        open_only = await search_service.search(_query(user_id, "отчёт", include_done=False))
        events = await search_service.search(_query(user_id, "Иван", kind=["event"]))
        later = await search_service.search(
            _query(user_id, "Иван", **{"from": (TOMORROW + timedelta(days=1)).isoformat()})
        )

        assert _titles(open_only) == ["Отчёт Ивану"]
        assert _titles(events) == ["Встреча с Иваном"]
        assert _titles(later) == []

    async def test_limit_is_respected(
        self, item_service: ItemService, search_service: SearchService, user_id: int
    ) -> None:
        for number in range(4):
            await _task(item_service, user_id, f"Позвонить Ивану {number}")

        result = await search_service.search(_query(user_id, "Иван", limit=2))

        assert len(result.results) == 2

    async def test_unknown_user_is_not_found(
        self, search_service: SearchService, user_id: int
    ) -> None:
        with pytest.raises(NotFoundError):
            await search_service.search(_query(user_id + 1, "Иван"))
