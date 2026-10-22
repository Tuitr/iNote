"""Фикстуры e2e: клиент к dev-роутам бота на поднятом стенде (make up / make e2e)."""

from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from tests.e2e.stand import BOT_URL


# Примечание: клиент к dev-роутам бота; стенд не поднят — понятная ошибка, а не таймаут
@pytest_asyncio.fixture
async def bot() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=BOT_URL, timeout=5) as client:
        try:
            ready = await client.get("/health/ready")
        except httpx.TransportError as exc:
            pytest.fail(f"стенд не поднят ({BOT_URL}): make up или make e2e — {exc}")
        assert ready.status_code == 200, ready.text
        yield client
