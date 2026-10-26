"""GET /v1/dialog: история диалога для мини-приложения. ЧЕРНОВИК D: формат утверждается на G3."""

from typing import Annotated

from fastapi import APIRouter, Query, Request

from libs.contracts.api import DialogPage
from libs.contracts.errors import ErrorCode
from libs.core.services.dialog import DialogService
from services.api.current_user import CurrentUser
from services.api.docs import UNAUTHORIZED, VALIDATION, json_example
from services.http_errors import HttpError

MAX_CURSOR = 2**63 - 1

router = APIRouter(prefix="/v1", tags=["dialog (черновик до G3)"], responses=UNAUTHORIZED)


@router.get(
    "/dialog",
    description=(
        "ЧЕРНОВИК: формат истории диалога утверждается на G3 (КиП). Реплики от новых к старым; "
        "next_cursor передать в before, чтобы получить более старые."
    ),
    responses={200: json_example("dialog.response"), **VALIDATION},
)
async def get_dialog(
    user: CurrentUser,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    before: str | None = None,
) -> DialogPage:
    dialog_service: DialogService = request.app.state.dialog_service
    return await dialog_service.list_turns(user.user_id, user.settings.tz, _cursor(before), limit)


def _cursor(before: str | None) -> int | None:
    if before is None:
        return None
    # Курсор для фронта непрозрачный; внутри — id последней показанной реплики.
    # Больше bigint база не примет: такой курсор фронт получить от нас не мог.
    if not before.isdigit() or int(before) > MAX_CURSOR:
        raise HttpError(
            ErrorCode.VALIDATION_ERROR, "Неверный курсор", {"invalid": ["query.before"]}
        )
    return int(before)
