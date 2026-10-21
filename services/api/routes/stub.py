"""Заглушка API для мини-приложения (шаг 6 S0): отвечает примерами из contracts/examples/j5.

Нужна E, чтобы сгенерировать типы из OpenAPI и собрать моки до настоящего API.
Подпись initData и JWT здесь не проверяются: это S2. В S2 эндпоинты по одному
переезжают в настоящие роуты, а модуль удаляется.
"""

import json
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Response, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from libs.contracts.api import (
    AuthMaxRequest,
    AuthMaxResponse,
    ItemsByDayResponse,
    ItemsResponse,
    Me,
    MePatch,
)
from libs.contracts.errors import ErrorCode, HttpErrorResponse
from libs.contracts.items import Item, ItemKind
from libs.contracts.tools import DateOnly, SearchItemsResult
from services.api.errors import ApiError

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "contracts" / "examples" / "j5"


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _json_example(name: str) -> dict[str, Any]:
    return {"content": {"application/json": {"example": _example(name)}}}


def _error_example(code: ErrorCode, message: str) -> dict[str, Any]:
    body = {
        "error": {
            "code": code,
            "message": message,
            "details": {},
            "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
        }
    }
    return {"model": HttpErrorResponse, "content": {"application/json": {"example": body}}}


_UNAUTHORIZED = {401: _error_example(ErrorCode.UNAUTHORIZED, "Нужна авторизация")}
_VALIDATION = {400: _error_example(ErrorCode.VALIDATION_ERROR, "Неверные поля запроса")}

_bearer = HTTPBearer(auto_error=False)


def require_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    # Заглушка принимает любой токен: важно только, что он передан, чтобы фронт
    # с первого дня ходил с заголовком Authorization и обрабатывал 401.
    if credentials is None:
        raise ApiError(ErrorCode.UNAUTHORIZED, "Нужна авторизация")


router = APIRouter(prefix="/v1")
protected = APIRouter(dependencies=[Depends(require_token)], responses=_UNAUTHORIZED)


@router.post(
    "/auth/max",
    response_model=AuthMaxResponse,
    responses={200: _json_example("auth_max.response"), **_VALIDATION},
    tags=["auth"],
)
async def auth_max(body: AuthMaxRequest) -> dict[str, Any]:
    return _example("auth_max.response")


@protected.get("/me", response_model=Me, responses={200: _json_example("me.response")}, tags=["me"])
async def get_me() -> dict[str, Any]:
    return _example("me.response")


@protected.patch(
    "/me",
    response_model=Me,
    responses={200: _json_example("me_patch.response"), **_VALIDATION},
    tags=["me"],
)
async def patch_me(body: MePatch) -> dict[str, Any]:
    return _example("me_patch.response")


@protected.get(
    "/items",
    response_model=ItemsByDayResponse | ItemsResponse,
    responses={
        200: {
            "content": {
                "application/json": {
                    "examples": {
                        "group=day": {"value": _example("items_by_day.response")},
                        "без group": {"value": _example("items.response")},
                    }
                }
            }
        },
        304: {"description": "Данные не изменились: If-None-Match совпал с ETag"},
        **_VALIDATION,
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
    body = _example("items_by_day.response" if group == "day" else "items.response")
    etag = f'"{body["version"]}"'
    if if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    response.headers["ETag"] = etag
    return body


@protected.get(
    "/items/search",
    response_model=SearchItemsResult,
    responses={200: _json_example("items_search.response"), **_VALIDATION},
    tags=["items"],
)
async def search_items(
    q: Annotated[str, Query(min_length=1)],
    limit: Annotated[int, Query(ge=1, le=10)] = 5,
) -> dict[str, Any]:
    return _example("items_search.response")


@protected.get(
    "/items/{item_id}",
    response_model=Item,
    responses={
        200: _json_example("item.response"),
        404: _error_example(ErrorCode.NOT_FOUND, "Запись не найдена"),
        **_VALIDATION,
    },
    tags=["items"],
)
async def get_item(item_id: UUID) -> dict[str, Any]:
    item = _example("item.response")
    if str(item_id) != item["id"]:
        raise ApiError(ErrorCode.NOT_FOUND, "Запись не найдена", {"id": str(item_id)})
    return item


router.include_router(protected)
