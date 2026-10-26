"""Ответ /health/ready."""

from typing import Literal

from pydantic import BaseModel

CheckStatus = Literal["ok", "fail"]


class ReadinessResponse(BaseModel):
    status: Literal["ok", "degraded"]
    checks: dict[str, CheckStatus]
