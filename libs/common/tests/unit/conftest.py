from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from pydantic import SecretStr

from common.errors import AppError, ErrorCode, installErrorHandlers
from common.middleware import installMiddleware

SERVICE_TOKEN = "s" * 32


# Примечание: мини-приложение с общими middleware и обработчиками ошибок
def buildApp() -> FastAPI:
    app = FastAPI()
    installErrorHandlers(app)
    installMiddleware(app, serviceToken=SecretStr(SERVICE_TOKEN))

    # Примечание: обычный публичный роут
    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    # Примечание: непойманное исключение — должно стать 500 internal
    @app.get("/boom")
    async def boom() -> dict[str, str]:
        msg = "секретная деталь стектрейса"
        raise RuntimeError(msg)

    # Примечание: ошибка приложения со своим кодом
    @app.get("/missing")
    async def missing() -> dict[str, str]:
        raise AppError(ErrorCode.NOT_FOUND, "запись не найдена", {"item_id": "7f3a"})

    # Примечание: роут с валидацией параметра пути
    @app.get("/items/{itemId}")
    async def item(itemId: int) -> dict[str, int]:
        return {"item_id": itemId}

    # Примечание: внутренний путь под сервисным токеном
    @app.get("/internal/v1/ping")
    async def internalPing() -> dict[str, str]:
        return {"status": "pong"}

    return app


# Примечание: httpx-клиент поверх ASGI без сети; исключения приложения не пробрасываем
@pytest_asyncio.fixture
async def appClient() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=buildApp(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


# Примечание: заголовок с верным сервисным токеном
@pytest.fixture
def authHeader() -> dict[str, str]:
    return {"Authorization": f"Bearer {SERVICE_TOKEN}"}
