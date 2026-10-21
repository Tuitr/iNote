"""MCP-контейнер: FastAPI с /health, служебным REST /internal/v1 (J8) и MCP на /mcp (J3)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from libs.core.config import load_default_tz, load_service_token
from libs.core.resources import create_session_factory
from libs.core.services.dialog import DialogService
from libs.core.services.items import ItemService
from libs.core.services.users import UserService
from services.http_common import health_router
from services.http_common import lifespan as common_lifespan
from services.http_errors import install_error_handlers
from services.mcp.internal import router as internal_router
from services.mcp.server import build_server
from services.mcp.service_token import ServiceTokenMiddleware
from services.mcp.tools.stub import EXAMPLES_DIR, load_examples

# Защита от DNS rebinding в SDK по умолчанию пускает только localhost, а агент C
# ходит по имени сервиса в сети compose: http://mcp:8000/mcp (КиП, «Транспорт»).
_TRANSPORT_SECURITY = TransportSecuritySettings(
    allowed_hosts=["mcp:*", "localhost:*", "127.0.0.1:*"],
    allowed_origins=["http://mcp:*", "http://localhost:*", "http://127.0.0.1:*"],
)


def create_app() -> FastAPI:
    # ItemService появится в app.state только при старте (lifespan), а сервер нужен раньше,
    # поэтому сервер получает функцию, которая берёт сервис в момент вызова инструмента.
    mcp_server = build_server(lambda: app.state.item_service, load_examples(EXAMPLES_DIR))
    mcp_http_app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp", transport_security=_TRANSPORT_SECURITY
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with common_lifespan(app), mcp_server.session_manager.run():
            app.state.service_token = load_service_token()
            session_factory = create_session_factory(app.state.db_engine)
            app.state.user_service = UserService(session_factory, load_default_tz())
            app.state.item_service = ItemService(session_factory, app.state.redis_client)
            app.state.dialog_service = DialogService(session_factory)
            yield

    app = FastAPI(title="iNote MCP", lifespan=lifespan)
    install_error_handlers(app)
    app.add_middleware(ServiceTokenMiddleware)
    app.include_router(health_router)
    app.include_router(internal_router)
    # Монтируется последним: запросы, которые не совпали с маршрутами выше, уходят в MCP.
    app.mount("/", mcp_http_app)
    return app


app = create_app()
