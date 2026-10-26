"""Кто делает запрос к /v1: пользователь из JWT (Authorization: Bearer <token>).

user_id берётся только отсюда, а не из параметров запроса фронта: иначе фронт мог бы
прочитать чужие записи.
"""

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from libs.contracts.errors import ErrorCode
from libs.core.services.time import now_utc
from libs.core.services.users import UserProfile, UserService
from services.api.auth import AuthError, read_token
from services.http_errors import HttpError

_bearer = HTTPBearer(auto_error=False)


async def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> UserProfile:
    if credentials is None:
        raise HttpError(ErrorCode.UNAUTHORIZED, "Нужна авторизация")
    secret: str = request.app.state.auth_settings.jwt_secret
    try:
        max_user_id = read_token(credentials.credentials, secret, now_utc())
    except AuthError as error:
        raise HttpError(ErrorCode.UNAUTHORIZED, str(error)) from error
    user_service: UserService = request.app.state.user_service
    profile = await user_service.find_profile(max_user_id)
    if profile is None:
        raise HttpError(ErrorCode.UNAUTHORIZED, "Пользователь не найден")
    return profile


CurrentUser = Annotated[UserProfile, Depends(current_user)]
