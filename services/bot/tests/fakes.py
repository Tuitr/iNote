"""Фейки портов бота для тестов. MaxGateway — настоящий FakeMaxGateway из infrastructure."""

import json
from pathlib import Path
from typing import Final

from bot.domain.updates import IncomingUpdate, MaxUserId, UpdateId
from bot.infrastructure.max_mapper import mapRawUpdate

FIXTURES_DIR: Final = Path(__file__).parent / "fixtures" / "max_updates"
FIXTURE_NAMES: Final = ("text", "command", "voice", "callback", "bot_started")


# Примечание: сырой апдейт MAX из fixtures/max_updates/<name>.json
def loadMaxUpdate(name: str) -> dict[str, object]:
    raw: dict[str, object] = json.loads((FIXTURES_DIR / f"{name}.json").read_text())
    return raw


# Примечание: фикстура, уже переведённая в IncomingUpdate
def loadIncoming(name: str) -> IncomingUpdate:
    update = mapRawUpdate(loadMaxUpdate(name))
    assert update is not None
    return update


# Примечание: дедуп в памяти (вместо Redis SET NX)
class InMemoryDeduplicator:
    # Примечание: множество уже виденных id
    def __init__(self) -> None:
        self.seen: set[UpdateId] = set()

    # Примечание: True при первом появлении id
    async def isFirstSeen(self, updateId: UpdateId) -> bool:
        if updateId in self.seen:
            return False
        self.seen.add(updateId)
        return True


# Примечание: лимитер с настоящим подсчётом, но без окна по времени
class CountingRateLimiter:
    # Примечание: limit — сколько апдейтов на пользователя пропускаем
    def __init__(self, limit: int = 20) -> None:
        self.limit = limit
        self.counts: dict[MaxUserId, int] = {}

    # Примечание: True, пока счётчик не превысил лимит
    async def allow(self, maxUserId: MaxUserId) -> bool:
        self.counts[maxUserId] = self.counts.get(maxUserId, 0) + 1
        return self.counts[maxUserId] <= self.limit
