"""Таблицы переменных в README бота и агента совпадают с классами настроек (S4, шаг 6).

Устарели — `uv run python deploy/env_docs.py` (или `make env-docs`) и закоммитить README.
"""

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Final

from agent.infrastructure.settings import AgentSettings
from bot.infrastructure.settings import BotSettings

SCRIPT: Final = Path(__file__).resolve().parents[2] / "deploy" / "env_docs.py"


# Примечание: скрипт deploy/env_docs.py как модуль (deploy — не пакет)
def envDocs() -> ModuleType:
    spec = importlib.util.spec_from_file_location("env_docs", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Примечание: README бота и агента не расходятся с настройками
def test_readmeTablesUpToDate() -> None:
    assert envDocs().main(["--check"]) == 0


# Примечание: у каждой переменной есть описание — иначе в таблице пустая колонка «Зачем»
def test_everySettingDescribed() -> None:
    for settings in (BotSettings, AgentSettings):
        missing = [n for n, i in settings.model_fields.items() if not i.description]
        assert missing == [], f"{settings.__name__}: нет description у {missing}"
