"""ETag и кеш списков GET /v1/items (J5, J7) на настоящем Redis."""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.tools import CreateTaskArgs
from libs.core.db.models import ItemRow
from libs.core.services.items import ItemService
from services.api.auth import issue_token
from services.tests.init_data import JWT_SECRET

pytestmark = pytest.mark.anyio

LIST = "/v1/items?from=2026-10-19&to=2026-10-22"


def _auth() -> dict[str, str]:
    token, _ = issue_token(123456789, JWT_SECRET, datetime.now(UTC))
    return {"Authorization": f"Bearer {token}"}


def _create_through_app(client: TestClient, user_id: int, title: str) -> None:
    """Запись через ItemService приложения — с настоящим Redis, как это делает MCP."""
    item_service: ItemService = client.app.state.item_service
    args = CreateTaskArgs.model_validate(
        {
            "caller": {
                "user_id": user_id,
                "tz": "Europe/Moscow",
                "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
            },
            "title": title,
            "starts_at": "2026-10-20",
        }
    )
    client.portal.call(item_service.create_task, args)


def _titles(response: Any) -> list[str]:
    return [item["title"] for item in response.json()["items"]]


async def test_same_version_gives_304_without_body(api_client: TestClient, user_id: int) -> None:
    first = api_client.get(LIST, headers=_auth())

    second = api_client.get(LIST, headers={**_auth(), "If-None-Match": first.headers["ETag"]})

    assert first.status_code == 200
    assert first.headers["ETag"] == '"0"'
    assert second.status_code == 304
    assert second.content == b""
    assert second.headers["ETag"] == '"0"'


async def test_new_record_through_mcp_service_bumps_version_and_list_is_reread(
    api_client: TestClient, user_id: int
) -> None:
    before = api_client.get(LIST, headers=_auth())

    _create_through_app(api_client, user_id, "Купить молоко")
    after = api_client.get(LIST, headers={**_auth(), "If-None-Match": before.headers["ETag"]})

    assert after.status_code == 200
    assert after.headers["ETag"] == '"1"'
    assert after.json()["version"] == 1
    assert _titles(before) == []
    assert _titles(after) == ["Купить молоко"]


async def test_list_is_served_from_cache_until_version_changes(
    api_client: TestClient, user_id: int, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    _create_through_app(api_client, user_id, "Купить молоко")
    first = api_client.get(LIST, headers=_auth())
    # Меняем базу мимо сервиса, без новой версии: API должен ответить из кеша.
    async with session_factory() as session, session.begin():
        await session.execute(update(ItemRow).values(title="Изменено мимо версии"))

    cached = api_client.get(LIST, headers=_auth())
    _create_through_app(api_client, user_id, "Купить хлеб")
    fresh = api_client.get(LIST, headers=_auth())

    redis = api_client.app.state.redis_client
    keys = api_client.portal.call(redis.keys, f"va:v1:api:list:{user_id}:*")
    ttl = api_client.portal.call(redis.ttl, keys[0])
    assert cached.content == first.content
    assert sorted(_titles(fresh)) == ["Изменено мимо версии", "Купить хлеб"]
    assert 0 < ttl <= 300
    assert len(keys) == 2  # по ключу на версию 1 и 2
