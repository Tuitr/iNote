"""Реестр ключей Redis (docs/redis-keys.md) как данные: разбор таблицы и аудит живых ключей (S4).

Нужен тестам «реестр ↔ код», e2e-аудиту стенда и `make redis-check`: каждый ключ `va:v1:*`
должен подходить под строку реестра и жить с TTL > 0 (без TTL — только белый список
`common.redis.NO_TTL_WHITELIST`).
"""

import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from common.redis import NO_TTL_WHITELIST

# TTL из колонки реестра: «24 ч», «60 с», «10 мин», «7 дней», «1 день», «без TTL»
_TTL_RE: Final = re.compile(r"^(?P<value>\d+)\s*(?P<unit>с|мин|ч|день|дня|дней)$")
_UNIT_S: Final = {"с": 1, "мин": 60, "ч": 3600, "день": 86400, "дня": 86400, "дней": 86400}
_KEY_RE: Final = re.compile(r"^`va:v1:(?P<service>[a-z][a-z0-9_]*):(?P<entity>[a-z][a-z0-9_]*):")
NO_TTL: Final = "без ttl"
# Redis TTL: -1 — ключ без срока, -2 — ключа уже нет (истёк между SCAN и TTL)
TTL_NONE: Final = -1
TTL_GONE: Final = -2


# Примечание: строка реестра: сервис и сущность ключа, кто пишет, TTL в секундах (None — без TTL)
@dataclass(frozen=True)
class RegistryRow:
    service: str
    entity: str
    writers: str
    ttlS: int | None
    status: str

    # Примечание: префикс ключа без id — по нему сверяем живые ключи
    @property
    def prefix(self) -> str:
        return f"va:v1:{self.service}:{self.entity}:"

    # Примечание: ключ зоны C (пишут бот или агент) и уже в коде, а не план чужой зоны
    @property
    def ownedByC(self) -> bool:
        return ("бот" in self.writers or "агент" in self.writers) and "в коде" in self.status


# Примечание: TTL из колонки реестра в секундах; «без TTL» — None; непонятное — ошибка
def parseTtl(raw: str) -> int | None:
    text = raw.replace("*", "").strip().lower()
    if text == NO_TTL:
        return None
    match = _TTL_RE.fullmatch(text)
    if match is None:
        msg = f"не разобрать TTL реестра: {raw!r}"
        raise ValueError(msg)
    return int(match["value"]) * _UNIT_S[match["unit"]]


# Примечание: строки таблицы «Ключи» из markdown реестра
def parseRegistry(markdown: str) -> list[RegistryRow]:
    rows: list[RegistryRow] = []
    for line in markdown.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 6 or (key := _KEY_RE.match(cells[0])) is None:  # noqa: PLR2004
            continue
        rows.append(
            RegistryRow(
                service=key["service"],
                entity=key["entity"],
                writers=cells[1],
                ttlS=parseTtl(cells[3]),
                status=cells[5],
            )
        )
    return rows


# Примечание: реестр из файла репозитория
def loadRegistry(path: Path) -> list[RegistryRow]:
    return parseRegistry(path.read_text(encoding="utf-8"))


# Примечание: строка реестра для ключа (по сервису и сущности) или None
def rowFor(key: str, rows: Iterable[RegistryRow]) -> RegistryRow | None:
    return next((row for row in rows if key.startswith(row.prefix)), None)


# Примечание: проблемы живых ключей: не из реестра, без TTL вне белого списка, TTL больше реестра
def auditKeys(keys: Iterable[tuple[str, int]], rows: Iterable[RegistryRow]) -> list[str]:
    registry = list(rows)
    problems: list[str] = []
    for key, ttl in keys:
        if ttl == TTL_GONE:
            continue
        row = rowFor(key, registry)
        if row is None:
            problems.append(f"{key}: нет в docs/redis-keys.md")
            continue
        if ttl == TTL_NONE and (row.service, row.entity) not in NO_TTL_WHITELIST:
            problems.append(f"{key}: без TTL")
        elif row.ttlS is not None and ttl > row.ttlS:
            problems.append(f"{key}: TTL {ttl} с больше, чем в реестре ({row.ttlS} с)")
    return problems


# Lua: все ключи va:v1:* с TTL одним вызовом (SCAN по курсору внутри Redis, без KEYS)
_DUMP_LUA: Final = """
local out = {}
local cursor = '0'
repeat
  local page = redis.call('SCAN', cursor, 'MATCH', 'va:v1:*', 'COUNT', 1000)
  cursor = page[1]
  for _, key in ipairs(page[2]) do
    table.insert(out, key)
    table.insert(out, redis.call('TTL', key))
  end
until cursor == '0'
return out
"""


# Lua: MEMORY USAGE и число ключей va:v1:* по «сервис:сущность»
_USAGE_LUA: Final = """
local sizes, counts, kinds = {}, {}, {}
local cursor = '0'
repeat
  local page = redis.call('SCAN', cursor, 'MATCH', 'va:v1:*', 'COUNT', 1000)
  cursor = page[1]
  for _, key in ipairs(page[2]) do
    local kind = string.match(key, '^va:v1:([^:]+:[^:]+):')
    if kind then
      if not sizes[kind] then sizes[kind] = 0; counts[kind] = 0; table.insert(kinds, kind) end
      sizes[kind] = sizes[kind] + (redis.call('MEMORY', 'USAGE', key) or 0)
      counts[kind] = counts[kind] + 1
    end
  end
until cursor == '0'
local out = {}
for _, kind in ipairs(kinds) do
  table.insert(out, kind); table.insert(out, sizes[kind]); table.insert(out, counts[kind])
end
return out
"""


# Примечание: Redis стенда через `docker compose exec redis redis-cli` (наружу Redis не опубликован)
@dataclass(frozen=True)
class StandRedis:
    compose: tuple[str, ...]

    # Примечание: одна команда redis-cli; вывод — строки без пустых
    def cli(self, *args: str) -> list[str]:
        command = [*self.compose, "exec", "-T", "redis", "redis-cli", "--no-raw", *args]
        result = subprocess.run(command, capture_output=True, text=True, check=True)  # noqa: S603
        return [_unquote(line) for line in result.stdout.splitlines() if line.strip()]

    # Примечание: все ключи va:v1:* с TTL
    def dump(self) -> list[tuple[str, int]]:
        flat = self.cli("EVAL", _DUMP_LUA, "0")
        return [(flat[i], int(flat[i + 1])) for i in range(0, len(flat), 2)]

    # Примечание: значение параметра CONFIG GET (maxmemory, maxmemory-policy)
    def config(self, name: str) -> str:
        _, value = self.cli("CONFIG", "GET", name)
        return value

    # Примечание: поле INFO memory (used_memory и др.)
    def memory(self, field: str) -> int:
        command = [*self.compose, "exec", "-T", "redis", "redis-cli", "INFO", "memory"]
        out = subprocess.run(command, capture_output=True, text=True, check=True).stdout  # noqa: S603
        for line in out.splitlines():
            if line.startswith(f"{field}:"):
                return int(line.split(":", 1)[1])
        msg = f"нет {field} в INFO memory"
        raise KeyError(msg)

    # Примечание: байты (MEMORY USAGE) и число ключей по видам «сервис:сущность» — одним EVAL
    def usageByKind(self) -> dict[str, tuple[int, int]]:
        flat = self.cli("EVAL", _USAGE_LUA, "0")
        return {flat[i]: (int(flat[i + 1]), int(flat[i + 2])) for i in range(0, len(flat), 3)}


# Примечание: строка вывода redis-cli --no-raw: «1) "va:…"», «(integer) 5» → значение
def _unquote(line: str) -> str:
    text = re.sub(r"^\s*\d+\)\s*", "", line.strip())
    text = text.removeprefix("(integer) ")
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text
