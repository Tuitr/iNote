"""API чтения для мини-приложения: профиль, записи по дням, поиск, карточка, история (J5)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from libs.contracts.internal import DialogTurnsRequest
from libs.contracts.tools import CreateEventArgs, CreateTaskArgs, DeleteItemArgs
from libs.core.services.dialog import DialogService
from libs.core.services.items import ItemService
from libs.core.services.users import UserService
from libs.core.tests.fakes import FakeRedis
from services.api.auth import issue_token
from services.tests.init_data import JWT_SECRET

pytestmark = pytest.mark.anyio

MAX_USER_ID = 123456789
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "j5"
# Записи «прошлой недели» создаются от имени этого момента, чтобы не упереться в past_time.
EARLIER = datetime(2026, 10, 1, tzinfo=UTC)


def _auth(max_user_id: int = MAX_USER_ID, now: datetime | None = None) -> dict[str, str]:
    token, _ = issue_token(max_user_id, JWT_SECRET, now or datetime.now(UTC))
    return {"Authorization": f"Bearer {token}"}


def _caller(user_id: int) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "tz": "Europe/Moscow",
        "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
    }


@pytest.fixture
def items(session_factory: async_sessionmaker[AsyncSession]) -> ItemService:
    return ItemService(session_factory, FakeRedis(), clock=lambda: EARLIER)


async def _week_like_j5_example(items: ItemService, user_id: int) -> tuple[str, str]:
    """Те же записи, что в примере J5 items_by_day: встреча 19-го и напоминание 21-го."""
    meeting = await items.create_event(
        CreateEventArgs.model_validate(
            {
                "caller": _caller(user_id),
                "title": "Встреча с дизайнером",
                "starts_at": "2026-10-19T11:00:00+03:00",
                "participants": ["Дизайнер"],
            }
        )
    )
    reminder = await items.create_task(
        CreateTaskArgs.model_validate(
            {
                "caller": _caller(user_id),
                "title": "Позвонить Ивану",
                "kind": "reminder",
                "remind_at": "2026-10-21T10:00:00+03:00",
            }
        )
    )
    assert meeting.item is not None
    assert reminder.item is not None
    return str(meeting.item.id), str(reminder.item.id)


@pytest.mark.parametrize(
    "path",
    ["/v1/me", "/v1/items?from=2026-10-19&to=2026-10-22", "/v1/items/search?q=x", "/v1/dialog"],
)
async def test_requests_without_valid_token_are_unauthorized(
    api_client: TestClient, path: str
) -> None:
    expired = _auth(now=datetime.now(UTC) - timedelta(hours=2))
    forged = {"Authorization": "Bearer not-a-jwt"}

    for headers in ({}, expired, forged):
        response = api_client.get(path, headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "unauthorized"


async def test_me(api_client: TestClient, user_id: int) -> None:
    me = api_client.get("/v1/me", headers=_auth())

    assert me.status_code == 200
    assert me.json()["settings"]["tz"] == "Europe/Moscow"


async def test_group_by_day_reproduces_j5_example(
    api_client: TestClient, items: ItemService, user_id: int
) -> None:
    await _week_like_j5_example(items, user_id)
    expected = json.loads((EXAMPLES_DIR / "items_by_day.response.json").read_text("utf-8"))

    response = api_client.get("/v1/items?from=2026-10-19&to=2026-10-22&group=day", headers=_auth())

    body = response.json()
    assert response.status_code == 200
    assert body["range"] == expected["range"]
    assert isinstance(body["version"], int)
    assert [(day["date"], day["label"]) for day in body["days"]] == [
        (day["date"], day["label"]) for day in expected["days"]
    ]
    assert [[item["title"] for item in day["items"]] for day in body["days"]] == [
        [item["title"] for item in day["items"]] for day in expected["days"]
    ]
    meeting = body["days"][0]["items"][0]
    assert (meeting["starts_at"], meeting["ends_at"]) == (
        "2026-10-19T11:00:00+03:00",
        "2026-10-19T12:00:00+03:00",
    )


async def test_flat_list_kind_filter_and_bad_period(
    api_client: TestClient, items: ItemService, user_id: int
) -> None:
    await _week_like_j5_example(items, user_id)

    flat = api_client.get("/v1/items?from=2026-10-19&to=2026-10-22", headers=_auth())
    events = api_client.get("/v1/items?from=2026-10-19&to=2026-10-22&kind=event", headers=_auth())
    reversed_period = api_client.get("/v1/items?from=2026-10-22&to=2026-10-19", headers=_auth())

    assert [item["title"] for item in flat.json()["items"]] == [
        "Встреча с дизайнером",
        "Позвонить Ивану",
    ]
    assert [item["title"] for item in events.json()["items"]] == ["Встреча с дизайнером"]
    assert reversed_period.status_code == 400
    assert reversed_period.json()["error"]["details"] == {"invalid": ["query.to"]}


async def test_search_and_item_card(
    api_client: TestClient,
    items: ItemService,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    meeting_id, reminder_id = await _week_like_j5_example(items, user_id)
    other = await UserService(session_factory, "Europe/Moscow", FakeRedis()).resolve(2, None, None)
    foreign = await items.create_task(
        CreateTaskArgs.model_validate({"caller": _caller(other.user_id), "title": "Чужое"})
    )
    assert foreign.item is not None
    await items.delete_item(
        DeleteItemArgs.model_validate({"caller": _caller(user_id), "id": reminder_id})
    )

    search = api_client.get("/v1/items/search?q=дизайнер", headers=_auth())
    own = api_client.get(f"/v1/items/{meeting_id}", headers=_auth())
    deleted = api_client.get(f"/v1/items/{reminder_id}", headers=_auth())
    alien = api_client.get(f"/v1/items/{foreign.item.id}", headers=_auth())
    malformed = api_client.get("/v1/items/not-a-uuid", headers=_auth())

    assert [hit["item"]["id"] for hit in search.json()["results"]] == [meeting_id]
    assert own.status_code == 200
    assert own.json()["title"] == "Встреча с дизайнером"
    assert (deleted.status_code, alien.status_code) == (404, 404)
    assert alien.json()["error"]["code"] == "not_found"
    assert malformed.status_code == 400


async def test_dialog_pages_from_newest(
    api_client: TestClient,
    items: ItemService,
    user_id: int,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    _, reminder_id = await _week_like_j5_example(items, user_id)
    await DialogService(session_factory).record_turns(
        DialogTurnsRequest.model_validate(
            {
                "user_id": user_id,
                "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
                "turns": [
                    {"role": "user", "text": "Напомни позвонить Ивану", "input_type": "voice"},
                    {"role": "assistant", "text": "Напомню.", "item_ids": [reminder_id]},
                ],
            }
        )
    )

    first = api_client.get("/v1/dialog?limit=1", headers=_auth()).json()
    second = api_client.get(
        f"/v1/dialog?limit=1&before={first['next_cursor']}", headers=_auth()
    ).json()
    bad = api_client.get("/v1/dialog?before=abc", headers=_auth())
    too_big = api_client.get(f"/v1/dialog?before={2**63}", headers=_auth())

    assert [turn["role"] for turn in first["turns"]] == ["assistant"]
    assert [brief["id"] for brief in first["turns"][0]["items"]] == [reminder_id]
    assert first["turns"][0]["created_at"].endswith("+03:00")
    assert first["next_cursor"] is not None
    assert [turn["text"] for turn in second["turns"]] == ["Напомни позвонить Ивану"]
    assert second["next_cursor"] is None
    assert (bad.status_code, too_big.status_code) == (400, 400)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PATCH", "/v1/me"),
        ("PATCH", "/v1/items/7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90"),
        ("DELETE", "/v1/items/7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90"),
        ("POST", "/v1/items"),
        ("PUT", "/v1/items/7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90"),
    ],
)
async def test_writes_from_mini_app_are_not_allowed(
    api_client: TestClient, user_id: int, method: str, path: str
) -> None:
    response = api_client.request(method, path, headers=_auth(), json={})

    assert response.status_code == 405


async def test_period_longer_than_62_days_is_rejected(api_client: TestClient, user_id: int) -> None:
    # Запрос «с 0001 по 9999 год» иначе построил бы миллионы дней в памяти.
    ok = api_client.get("/v1/items?from=2026-10-01&to=2026-12-02&group=day", headers=_auth())
    too_long = api_client.get("/v1/items?from=2026-10-01&to=2026-12-03", headers=_auth())
    absurd = api_client.get("/v1/items?from=0001-01-01&to=9999-12-31&group=day", headers=_auth())

    assert ok.status_code == 200
    assert len(ok.json()["days"]) == 62
    assert (too_long.status_code, absurd.status_code) == (400, 400)
    assert too_long.json()["error"]["details"] == {"invalid": ["query.to"]}
