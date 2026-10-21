import pytest
from services.bot.tests.fakes import CountingRateLimiter, InMemoryDeduplicator

from bot.application.handle_update import HandleUpdate
from bot.infrastructure.fake_max import FakeMaxGateway
from bot.presentation.templates import RussianTexts


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


# Примечание: use case на фейках
@pytest.fixture
def handler(
    gateway: FakeMaxGateway, dedup: InMemoryDeduplicator, limiter: CountingRateLimiter
) -> HandleUpdate:
    return HandleUpdate(
        gateway=gateway, deduplicator=dedup, rateLimiter=limiter, texts=RussianTexts()
    )
