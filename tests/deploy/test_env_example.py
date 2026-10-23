"""Настройки ↔ .env.example (S4): каждая переменная бота и агента и каждая общая есть в
.env.example, и наоборот — в .env.example нет переменных BOT_/AGENT_/общих, которых не читает код.

Исключения — только переменные compose (их читает docker compose, а не сервис) с причиной.
"""

import re
from pathlib import Path
from typing import Final

from agent.infrastructure.settings import AgentSettings
from bot.infrastructure.settings import BotSettings
from common.config import SHARED_ENV_NAMES

ENV_EXAMPLE: Final = Path(__file__).resolve().parents[2] / ".env.example"
_LINE_RE: Final = re.compile(r"^(?P<name>[A-Z][A-Z0-9_]*)=")
# Переменные compose, не сервиса: порт публикации бота для /dev/max/* (deploy/docker-compose.yml)
COMPOSE_ONLY: Final = frozenset({"BOT_DEV_PORT"})
# Общие, которые зона C не читает, но которые нужны другим сервисам (D: PostgreSQL)
SHARED_FOR_OTHERS: Final = frozenset({"DATABASE_URL"})


# Примечание: имена переменных из .env.example (строки NAME=…, комментарии пропускаем)
def exampleNames() -> set[str]:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    return {m["name"] for line in lines if (m := _LINE_RE.match(line))}


# Примечание: переменные, которые читает код бота и агента (alias полей настроек)
def settingsNames() -> set[str]:
    return set(BotSettings.envNames() | AgentSettings.envNames())


# Примечание: всё, что читают настройки бота и агента, есть в .env.example
def test_everySettingInEnvExample() -> None:
    missing = settingsNames() - exampleNames()
    assert missing == set(), f"нет в .env.example: {sorted(missing)}"


# Примечание: в .env.example нет BOT_/AGENT_/общих переменных, которых не читает код
def test_noStaleVariablesInEnvExample() -> None:
    ours = {
        name
        for name in exampleNames()
        if name.startswith(("BOT_", "AGENT_")) or name in SHARED_ENV_NAMES
    }
    stale = ours - settingsNames() - COMPOSE_ONLY - SHARED_FOR_OTHERS
    assert stale == set(), f"в .env.example, но код не читает: {sorted(stale)}"


# Примечание: все общие переменные стандарта (CLAUDE.md, правило 10) перечислены в .env.example
def test_sharedVariablesListed() -> None:
    assert exampleNames() >= SHARED_ENV_NAMES


# Примечание: исключения не устаревают — compose правда использует BOT_DEV_PORT
def test_composeOnlyStillUsed() -> None:
    compose = (ENV_EXAMPLE.parent / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert all(f"${{{name}" in compose for name in COMPOSE_ONLY)
