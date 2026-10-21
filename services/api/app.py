"""HTTP API для мини-приложения (J5): /v1/auth/max настоящий, остальное /v1 пока заглушка."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.core.config import load_api_auth_settings, load_default_tz
from libs.core.resources import create_session_factory
from libs.core.services.users import UserService
from services.api.routes.auth import router as auth_router
from services.api.routes.stub import router as stub_router
from services.http_common import health_router
from services.http_common import lifespan as common_lifespan
from services.http_errors import install_error_handlers

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with common_lifespan(app):
        app.state.auth_settings = load_api_auth_settings()
        if app.state.auth_settings.dev_auth:
            logger.warning(
                "API_DEV_AUTH=true: подпись initData не проверяется, только для локальной работы"
            )
        session_factory = create_session_factory(app.state.db_engine)
        app.state.user_service = UserService(session_factory, load_default_tz())
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="iNote API", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(stub_router)
    return app


app = create_app()
