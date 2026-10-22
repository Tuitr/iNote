"""Служебный REST для агента C (J8): /internal/v1. Наружу не публикуется (КиП)."""

from fastapi import APIRouter, Request, Response, status

from libs.contracts.internal import (
    DialogTurnsRequest,
    ResolveUserRequest,
    ResolveUserResponse,
    UserSettingsPatch,
)
from libs.contracts.settings import UserSettings
from libs.core.services.dialog import DialogService
from libs.core.services.users import UserService

router = APIRouter(prefix="/internal/v1", tags=["internal"])


@router.post("/users/resolve")
async def resolve_user(body: ResolveUserRequest, request: Request) -> ResolveUserResponse:
    user_service: UserService = request.app.state.user_service
    resolved = await user_service.resolve(body.max_user_id, body.max_chat_id, body.name)
    return ResolveUserResponse(
        user_id=resolved.user_id,
        tz=resolved.settings.tz,
        settings=resolved.settings,
        created=resolved.created,
    )


@router.patch("/users/{user_id}/settings")
async def update_user_settings(
    user_id: int, body: UserSettingsPatch, request: Request
) -> UserSettings:
    """Пользователь меняет настройки в разговоре с ботом («у меня теперь Екатеринбург»)."""
    user_service: UserService = request.app.state.user_service
    # null в теле значит «не менять» (контракт UserSettingsPatch).
    return await user_service.update_settings(user_id, body.model_dump(exclude_none=True))


@router.post("/dialog-turns", status_code=status.HTTP_202_ACCEPTED)
async def record_dialog_turns(body: DialogTurnsRequest, request: Request) -> Response:
    dialog_service: DialogService = request.app.state.dialog_service
    await dialog_service.record_turns(body)
    return Response(status_code=status.HTTP_202_ACCEPTED)
