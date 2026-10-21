"""HTTP API для мини-приложения (J5): вход, профиль, записи, поиск и история диалога."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from libs.core.config import load_api_auth_settings, load_default_tz
from libs.core.resources import create_session_factory
from libs.core.services.dialog import DialogService
from libs.core.services.items import ItemService
from libs.core.services.search import SearchService
from libs.core.services.users import UserService
from services.api.routes.auth import router as auth_router
from services.api.routes.dialog import router as dialog_router
from services.api.routes.items import router as items_router
from services.api.routes.me import router as me_router
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
        redis = app.state.redis_client
        app.state.user_service = UserService(session_factory, load_default_tz(), redis)
        app.state.item_service = ItemService(session_factory, redis)
        app.state.search_service = SearchService(session_factory)
        app.state.dialog_service = DialogService(session_factory)
        yield


def create_app() -> FastAPI:
    app = FastAPI(title="iNote API", version="0.1.0", lifespan=lifespan)
    install_error_handlers(app)
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(me_router)
    app.include_router(items_router)
    app.include_router(dialog_router)
    return app


app = create_app()
