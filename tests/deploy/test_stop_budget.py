"""Бюджет остановки (S4, шаг 2): сервис зоны C успевает остановиться штатно, до SIGKILL от compose.

После SIGTERM uvicorn ждёт начатые HTTP-запросы до --timeout-graceful-shutdown, затем lifespan
дожидается фоновых задач до своего таймаута (бот — BOT_SHUTDOWN_TIMEOUT_S, агент — таймаут
реестра из libs/common). Сумма плюс запас должна быть меньше stop_grace_period в compose.
"""

import re
from pathlib import Path
from typing import Final

from bot.infrastructure.settings import BotSettings
from common.lifecycle import DEFAULT_SHUTDOWN_TIMEOUT_S

DEPLOY: Final = Path(__file__).resolve().parents[2] / "deploy"
# запас на закрытие клиентов, MCP-сессии и выход интерпретатора
MARGIN_S: Final = 3.0


# Примечание: --timeout-graceful-shutdown из CMD общего образа Python-сервисов
def gracefulS() -> float:
    dockerfile = (DEPLOY / "docker" / "python.Dockerfile").read_text(encoding="utf-8")
    match = re.search(r"--timeout-graceful-shutdown (\d+)", dockerfile)
    assert match, "в CMD нет --timeout-graceful-shutdown"
    return float(match.group(1))


# Примечание: stop_grace_period якоря x-python-service в compose (секунды)
def gracePeriodS(compose: str) -> float:
    match = re.search(r"stop_grace_period: (\d+)s", compose)
    assert match, "в compose нет stop_grace_period"
    return float(match.group(1))


# Примечание: таймаут фоновых задач бота по умолчанию (агент — реестр libs/common)
def botShutdownS() -> float:
    default = BotSettings.model_fields["shutdownTimeoutS"].default
    assert isinstance(default, float)
    return default


# Примечание: uvicorn + задачи + запас укладываются в stop_grace_period у бота и агента
def test_stopFitsGracePeriod() -> None:
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    worst = gracefulS() + max(botShutdownS(), DEFAULT_SHUTDOWN_TIMEOUT_S) + MARGIN_S
    assert worst <= gracePeriodS(compose), f"остановка до {worst} с > stop_grace_period"


# Примечание: стенд с HTTPS не укорачивает stop_grace_period (override его не задаёт или больше)
def test_standDoesNotShortenGrace() -> None:
    stand = (DEPLOY / "docker-compose.stand.yml").read_text(encoding="utf-8")
    base = gracePeriodS((DEPLOY / "docker-compose.yml").read_text(encoding="utf-8"))
    for value in re.findall(r"stop_grace_period: (\d+)s", stand):
        assert float(value) >= base
