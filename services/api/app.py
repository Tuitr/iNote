"""HTTP API для мини-приложения (J5). Пока /v1 отвечает заглушка с примерами."""

from fastapi import FastAPI

from services.api.routes.stub import router as stub_router
from services.http_common import health_router, lifespan
from services.http_errors import install_error_handlers

app = FastAPI(title="iNote API", version="0.1.0", lifespan=lifespan)
install_error_handlers(app)
app.include_router(health_router)
app.include_router(stub_router)
