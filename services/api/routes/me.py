"""GET /v1/me: имя и настройки пользователя (J5). Мини-приложение только читает."""

from fastapi import APIRouter

from libs.contracts.api import Me
from services.api.current_user import CurrentUser
from services.api.docs import UNAUTHORIZED, json_example

router = APIRouter(prefix="/v1", tags=["me"], responses=UNAUTHORIZED)


@router.get("/me", responses={200: json_example("me.response")})
async def get_me(user: CurrentUser) -> Me:
    return Me(name=user.name or "", settings=user.settings)
