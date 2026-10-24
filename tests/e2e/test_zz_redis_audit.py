"""E2E-аудит Redis стенда (S4, шаг 3): идёт последним в tests/e2e (имя файла), после сценариев.

Каждый ключ `va:v1:*` подходит под строку docs/redis-keys.md и живёт с TTL > 0 (без TTL — только
`va:v1:mcp:ver:*`, его пишет MCP D, не мы); `maxmemory` задан, политика `volatile-lru`.
Redis наружу не опубликован — читаем через `docker compose exec redis redis-cli`.
"""

from pathlib import Path
from typing import Final

import pytest

from common.testing.redis_registry import StandRedis, auditKeys, loadRegistry

pytestmark = pytest.mark.e2e

ROOT: Final = Path(__file__).resolve().parents[2]
COMPOSE: Final = (
    "docker",
    "compose",
    "-f",
    str(ROOT / "deploy" / "docker-compose.yml"),
    "--env-file",
    str(ROOT / ".env"),
    "--profile",
    "mock",
)


# Примечание: Redis стенда; без docker compose — понятный пропуск невозможен: e2e требует стенд
@pytest.fixture(scope="module")
def standRedis() -> StandRedis:
    return StandRedis(COMPOSE)


# Примечание: ключи после сценариев — все из реестра и с TTL; без TTL — только версия данных D
# va:v1:mcp:ver:{user_id} (белый список реестра): на стенде настоящий D, и он её пишет
def test_keysInRegistryWithTtl(standRedis: StandRedis) -> None:
    keys = standRedis.dump()
    assert keys, "в Redis стенда нет ключей va:v1:* — сценарии не прогнаны?"
    assert auditKeys(keys, loadRegistry(ROOT / "docs" / "redis-keys.md")) == []
    assert any(key.startswith("va:v1:mcp:ver:") for key, _ in keys)


# Примечание: maxmemory задан (256 МБ из compose), вытеснение — только ключей с TTL
def test_memoryPolicy(standRedis: StandRedis) -> None:
    assert int(standRedis.config("maxmemory")) == 256 * 1024 * 1024
    assert standRedis.config("maxmemory-policy") == "volatile-lru"
