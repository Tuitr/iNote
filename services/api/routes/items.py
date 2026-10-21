"""Записи для мини-приложения: список за период (по дням), поиск, карточка (J5)."""

import uuid
from datetime import date, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request

from libs.contracts.api import ItemsByDayResponse, ItemsDay, ItemsRange, ItemsResponse
from libs.contracts.errors import ErrorCode
from libs.contracts.items import Item, ItemKind
from libs.contracts.tools import Caller, DateOnly, SearchItemsArgs, SearchItemsResult
from libs.core.services.items import ItemService
from libs.core.services.search import SearchService
from libs.core.services.time import day_label
from libs.core.services.versioning import read_version
from services.api.current_user import CurrentUser
from services.api.docs import UNAUTHORIZED, VALIDATION, error_example, example, json_example
from services.http_errors import HttpError

# Защита от запроса «за десять лет»: экран показывает дни и недели, столько записей не нужно.
MAX_API_ITEMS = 500

router = APIRouter(prefix="/v1", tags=["items"], responses=UNAUTHORIZED)


@router.get(
    "/items",
    response_model=ItemsByDayResponse | ItemsResponse,
    responses={
        200: {
            "content": {
                "application/json": {
                    "examples": {
                        "group=day": {"value": example("items_by_day.response")},
                        "без group": {"value": example("items.response")},
                    }
                }
            }
        },
        304: {"description": "Данные не изменились: If-None-Match совпал с ETag"},
        **VALIDATION,
    },
)
async def list_items(
    user: CurrentUser,
    request: Request,
    from_: Annotated[DateOnly, Query(alias="from")],
    to: DateOnly,
    kind: Annotated[list[ItemKind] | None, Query()] = None,
    item_status: Annotated[Literal["open", "done", "all"], Query(alias="status")] = "open",
    group: Literal["day"] | None = None,
    # Есть в контракте J5 с S0; ответ 304 по нему — шаг 23 (ETag).
    if_none_match: Annotated[str | None, Header()] = None,
) -> ItemsByDayResponse | ItemsResponse:
    if to <= from_:
        raise HttpError(
            ErrorCode.VALIDATION_ERROR,
            "Конец периода должен быть позже начала",
            {"invalid": ["query.to"]},
        )
    item_service: ItemService = request.app.state.item_service
    tz = user.settings.tz
    kinds = [value.value for value in kind] if kind else None
    items = await item_service.list_period(
        user.user_id, tz, from_, to, kinds, item_status, MAX_API_ITEMS
    )
    period = ItemsRange(from_=from_, to=to, tz=tz)
    version = await read_version(request.app.state.redis_client, user.user_id)
    if group == "day":
        return ItemsByDayResponse(range=period, version=version, days=_by_day(from_, to, items))
    return ItemsResponse(range=period, version=version, items=items)


@router.get("/items/search", responses={200: json_example("items_search.response"), **VALIDATION})
async def search_items(
    user: CurrentUser,
    request: Request,
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> SearchItemsResult:
    search_service: SearchService = request.app.state.search_service
    # Тот же поиск, что у инструмента MCP (J5): API начинает свою цепочку trace_id (КиП).
    caller = Caller(user_id=user.user_id, tz=user.settings.tz, trace_id=uuid.uuid4().hex)
    return await search_service.search(SearchItemsArgs(caller=caller, query=q, limit=limit))


@router.get(
    "/items/{item_id}",
    responses={
        200: json_example("item.response"),
        404: error_example(ErrorCode.NOT_FOUND, "Запись не найдена"),
        **VALIDATION,
    },
)
async def get_item(item_id: UUID, user: CurrentUser, request: Request) -> Item:
    item_service: ItemService = request.app.state.item_service
    return await item_service.get_item(user.user_id, user.settings.tz, item_id)


def _by_day(start: date, end: date, items: list[Item]) -> list[ItemsDay]:
    """Все дни периода, включая пустые, с подписью «Пн, 19 октября» (J5).

    Записи приходят отсортированными («весь день» первыми, потом по времени), порядок сохраняется.
    """
    by_date: dict[date, list[Item]] = {}
    for item in items:
        # В список за период попадают только записи со временем (ItemRepo.list_in_range).
        moment = item.starts_at or item.remind_at
        if moment is not None:
            by_date.setdefault(moment.date(), []).append(item)
    days = []
    day = start
    while day < end:
        label = day_label(day)
        days.append(
            ItemsDay(date=day, label=label[:1].upper() + label[1:], items=by_date.get(day, []))
        )
        day += timedelta(days=1)
    return days
