import logging
from collections.abc import Iterator

import pytest
import structlog


# Примечание: каждый тест логирования начинает с чистой конфигурации и не портит соседей
@pytest.fixture(autouse=True)
def resetLogging() -> Iterator[None]:
    root = logging.getLogger()
    savedHandlers, savedLevel = list(root.handlers), root.level
    yield
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    root.handlers, root.level = savedHandlers, savedLevel
