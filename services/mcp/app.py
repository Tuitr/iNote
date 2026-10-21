"""MCP-сервер (J3): FastAPI с /health и смонтированным MCP на /mcp.

Пока на /mcp отвечает заглушка с примерами (services/mcp/tools/stub.py).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from services.http_common import health_router
from services.http_common import lifespan as common_lifespan
from services.mcp.tools.stub import EXAMPLES_DIR, build_stub_server, load_examples

# Защита от DNS rebinding в SDK по умолчанию пускает только localhost, а агент C
# ходит по имени сервиса в сети compose: http://mcp:8000/mcp (КиП, «Транспорт»).
_TRANSPORT_SECURITY = TransportSecuritySettings(
    allowed_hosts=["mcp:*", "localhost:*", "127.0.0.1:*"],
    allowed_origins=["http://mcp:*", "http://localhost:*", "http://127.0.0.1:*"],
)


def create_app() -> FastAPI:
    mcp_server = build_stub_server(load_examples(EXAMPLES_DIR))
    mcp_http_app = mcp_server.streamable_http_app(
        streamable_http_path="/mcp", transport_security=_TRANSPORT_SECURITY
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with common_lifespan(app), mcp_server.session_manager.run():
            yield

    app = FastAPI(title="iNote MCP", lifespan=lifespan)
    app.include_router(health_router)
    # Монтируется последним: запросы, которые не совпали с /health, уходят в MCP.
    app.mount("/", mcp_http_app)
    return app


app = create_app()
