"""Общая HTTP-часть сервисов mcp и api: подключения на время жизни приложения и health."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response, status

from libs.contracts.health import ReadinessResponse
from libs.core.config import load_settings
from libs.core.health import check_readiness
from libs.core.log_setup import configure_logging
from libs.core.resources import create_db_engine, create_redis_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    configure_logging(settings.log_level)
    app.state.db_engine = create_db_engine(settings.database_url)
    app.state.redis_client = create_redis_client(settings.redis_url)
    try:
        yield
    finally:
        await app.state.redis_client.aclose()
        await app.state.db_engine.dispose()


health_router = APIRouter(prefix="/health", tags=["health"])


@health_router.get("/live")
async def health_live() -> dict[str, str]:
    return {"status": "ok"}


@health_router.get("/ready")
async def health_ready(request: Request, response: Response) -> ReadinessResponse:
    readiness = await check_readiness(request.app.state.db_engine, request.app.state.redis_client)
    if readiness.status != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return readiness
