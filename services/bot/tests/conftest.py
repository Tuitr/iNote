from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from bot.application.handle_update import BotDeps, HandleUpdate
from bot.application.reply_mapper import OpenAppMode, ReplyToMaxMapper
from bot.application.typing import TypingIndicator
from bot.infrastructure.fake_max import FakeMaxGateway
from bot.presentation.templates import RussianTexts
from common.lifecycle import TaskRegistry, managedTasks
from services.bot.tests.fakes import (
    CountingRateLimiter,
    FakeAgent,
    InMemoryCallbackStore,
    InMemoryDeduplicator,
)

BOT_USERNAME = "va_test_bot"


# Примечание: свежий фейковый MAX на тест
@pytest.fixture
def gateway() -> FakeMaxGateway:
    return FakeMaxGateway()


# Примечание: дедуп в памяти
@pytest.fixture
def dedup() -> InMemoryDeduplicator:
    return InMemoryDeduplicator()


# Примечание: лимитер с маленьким лимитом, чтобы легко его превысить
@pytest.fixture
def limiter() -> CountingRateLimiter:
    return CountingRateLimiter(limit=3)


# Примечание: агент по сценарию (по умолчанию — contracts/examples/reply/confirm.json)
@pytest.fixture
def agent() -> FakeAgent:
    return FakeAgent()


# Примечание: кнопки в памяти
@pytest.fixture
def callbacks() -> InMemoryCallbackStore:
    return InMemoryCallbackStore()


# Примечание: реестр фоновых задач на тест (для «печатает…»)
@pytest_asyncio.fixture
async def tasks() -> AsyncIterator[TaskRegistry]:
    async with managedTasks(timeoutS=1) as registry:
        yield registry


# Примечание: use case на фейках; «печатает…» — каждые 20 мс, чтобы тест видел повторы
@pytest.fixture
def handler(
    *,
    gateway: FakeMaxGateway,
    dedup: InMemoryDeduplicator,
    limiter: CountingRateLimiter,
    agent: FakeAgent,
    callbacks: InMemoryCallbackStore,
    tasks: TaskRegistry,
) -> HandleUpdate:
    mapper = ReplyToMaxMapper(callbacks, mode=OpenAppMode.OPEN_APP, botUsername=BOT_USERNAME)
    return HandleUpdate(
        BotDeps(
            gateway=gateway,
            deduplicator=dedup,
            rateLimiter=limiter,
            texts=RussianTexts(),
            agent=agent,
            callbacks=callbacks,
            mapper=mapper,
            typing=TypingIndicator(gateway, tasks, intervalS=0.02),
        )
    )
