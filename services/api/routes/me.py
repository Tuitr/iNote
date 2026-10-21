"""GET и PATCH /v1/me: имя и настройки пользователя (J5)."""

from fastapi import APIRouter, Request

from libs.contracts.api import Me, MePatch
from libs.core.services.users import UserService
from services.api.current_user import CurrentUser
from services.api.docs import UNAUTHORIZED, VALIDATION, json_example

router = APIRouter(prefix="/v1", tags=["me"], responses=UNAUTHORIZED)


@router.get("/me", responses={200: json_example("me.response")})
async def get_me(user: CurrentUser) -> Me:
    return Me(name=user.name or "", settings=user.settings)


@router.patch("/me", responses={200: json_example("me_patch.response"), **VALIDATION})
async def patch_me(body: MePatch, user: CurrentUser, request: Request) -> Me:
    user_service: UserService = request.app.state.user_service
    # null в теле значит «не менять» (контракт MePatch).
    settings = await user_service.update_settings(user.user_id, body.model_dump(exclude_none=True))
    return Me(name=user.name or "", settings=settings)
