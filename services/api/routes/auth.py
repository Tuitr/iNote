"""POST /v1/auth/max: initData из MAX → JWT, данные пользователя и start_param (J5)."""

from fastapi import APIRouter, Request

from libs.contracts.api import AuthMaxRequest, AuthMaxResponse, Me
from libs.contracts.errors import ErrorCode
from libs.core.config import ApiAuthSettings
from libs.core.services.time import now_utc, to_user_tz
from libs.core.services.users import UserService
from services.api.auth import AuthError, issue_token, read_init_data
from services.api.docs import UNAUTHORIZED, VALIDATION, json_example
from services.http_errors import HttpError

router = APIRouter(prefix="/v1", tags=["auth"])


@router.post(
    "/auth/max",
    responses={200: json_example("auth_max.response"), **VALIDATION, **UNAUTHORIZED},
)
async def auth_max(body: AuthMaxRequest, request: Request) -> AuthMaxResponse:
    settings: ApiAuthSettings = request.app.state.auth_settings
    user_service: UserService = request.app.state.user_service
    now = now_utc()
    try:
        login = read_init_data(
            body.init_data, settings.bot_token, now, check_signature=not settings.dev_auth
        )
    except AuthError as error:
        raise HttpError(ErrorCode.UNAUTHORIZED, str(error)) from error
    resolved = await user_service.resolve(login.max_user_id, None, login.name)
    token, expires_at = issue_token(login.max_user_id, settings.jwt_secret, now)
    return AuthMaxResponse(
        token=token,
        expires_at=to_user_tz(expires_at, resolved.settings.tz),
        me=Me(name=login.name, settings=resolved.settings),
        start_param=login.start_param,
    )
