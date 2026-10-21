"""Заглушка API для мини-приложения (шаг 6 S0): отвечает примерами из contracts/examples/j5.

Нужна E, чтобы сгенерировать типы из OpenAPI и собрать моки до настоящего API. Эндпоинты по одному
переезжают в настоящие роуты (/v1/auth/max — services/api/routes/auth.py), затем модуль удаляется.
"""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from libs.contracts.api import (
    ItemsByDayResponse,
    ItemsResponse,
    Me,
    MePatch,
)
from libs.contracts.errors import ErrorCode
from libs.contracts.items import Item, ItemKind
from libs.contracts.tools import DateOnly, SearchItemsResult
from services.api.docs import UNAUTHORIZED, VALIDATION, error_example, example, json_example
from services.http_errors import HttpError

_bearer = HTTPBearer(auto_error=False)


def require_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    # Заглушка принимает любой токен: важно только, что он передан, чтобы фронт
    # с первого дня ходил с заголовком Authorization и обрабатывал 401.
    if credentials is None:
        raise HttpError(ErrorCode.UNAUTHORIZED, "Нужна авторизация")


router = APIRouter(prefix="/v1")
protected = APIRouter(dependencies=[Depends(require_token)], responses=UNAUTHORIZED)


@protected.get("/me", response_model=Me, responses={200: json_example("me.response")}, tags=["me"])
async def get_me() -> dict[str, Any]:
    return example("me.response")


@protected.patch(
    "/me",
    response_model=Me,
    responses={200: json_example("me_patch.response"), **VALIDATION},
    tags=["me"],
)
async def patch_me(body: MePatch) -> dict[str, Any]:
    return example("me_patch.response")


@protected.get(
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
    tags=["items"],
)
async def list_items(
    response: Response,
    from_: Annotated[DateOnly, Query(alias="from")],
    to: DateOnly,
    kind: Annotated[list[ItemKind] | None, Query()] = None,
    item_status: Annotated[Literal["open", "done", "all"], Query(alias="status")] = "open",
    group: Literal["day"] | None = None,
    if_none_match: Annotated[str | None, Header()] = None,
) -> dict[str, Any] | Response:
    body = example("items_by_day.response" if group == "day" else "items.response")
    etag = f'"{body["version"]}"'
    if if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return body


@protected.get(
    "/items/search",
    response_model=SearchItemsResult,
    responses={200: json_example("items_search.response"), **VALIDATION},
    tags=["items"],
)
async def search_items(
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> dict[str, Any]:
    return example("items_search.response")


@protected.get(
    "/items/{item_id}",
    response_model=Item,
    responses={
        200: json_example("item.response"),
        404: error_example(ErrorCode.NOT_FOUND, "Запись не найдена"),
        **VALIDATION,
    },
    tags=["items"],
)
async def get_item(item_id: UUID) -> dict[str, Any]:
    item = example("item.response")
    if str(item_id) != item["id"]:
        raise HttpError(ErrorCode.NOT_FOUND, "Запись не найдена", {"id": str(item_id)})
    return item


router.include_router(protected)
