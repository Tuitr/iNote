"""Ответ /health/ready (КиП, раздел про health-эндпоинты)."""

from typing import Literal

from pydantic import BaseModel

CheckStatus = Literal["ok", "fail"]


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, CheckStatus]
