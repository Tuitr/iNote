"""`make redis-check`: аудит Redis поднятого стенда по реестру docs/redis-keys.md (S4, шаг 3).

Каждый ключ `va:v1:*` — из реестра и с TTL > 0 (без TTL — только белый список); `maxmemory`
задан, политика `volatile-lru`. Ключ `va:v1:mcp:ver:*` зона C не пишет — если он есть, это MCP D.
Выход 1 — есть проблемы (список — в stdout).

    uv run python deploy/redis_check.py [команда compose …]
"""

import shlex
import sys
from collections import Counter
from pathlib import Path
from typing import Final

from common.testing.redis_registry import StandRedis, auditKeys, loadRegistry, rowFor

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE: Final = (
    f"docker compose -f {ROOT / 'deploy' / 'docker-compose.yml'} "
    f"--env-file {ROOT / '.env'} --profile mock"
)
POLICY: Final = "volatile-lru"


# Примечание: проблемы конфигурации Redis: нет maxmemory или чужая политика вытеснения
def configProblems(redis: StandRedis) -> list[str]:
    problems = []
    if int(redis.config("maxmemory")) <= 0:
        problems.append("maxmemory не задан")
    if (policy := redis.config("maxmemory-policy")) != POLICY:
        problems.append(f"maxmemory-policy={policy}, ожидается {POLICY}")
    return problems


# Примечание: отчёт: сколько ключей каждого вида, затем проблемы; код выхода — есть ли они
def main(argv: list[str]) -> int:
    compose = tuple(shlex.split(" ".join(argv) if argv else DEFAULT_COMPOSE))
    redis = StandRedis(compose)
    rows = loadRegistry(ROOT / "docs" / "redis-keys.md")
    keys = redis.dump()
    kinds = Counter(
        f"{row.service}:{row.entity}" if (row := rowFor(key, rows)) else "?" for key, _ in keys
    )
    for kind, count in sorted(kinds.items()):
        print(f"  {kind:<18} {count}")  # noqa: T201 — отчёт утилиты
    problems = configProblems(redis) + auditKeys(keys, rows)
    for problem in problems:
        print(f"FAIL {problem}")  # noqa: T201
    print(f"ключей: {len(keys)}, проблем: {len(problems)}")  # noqa: T201
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
