"""Шаблоны ↔ коды и код (S4): у каждого кода ошибки стандарта, Brain и инструмента — свой текст;
каждый шаблон где-то используется; в [errors] нет текстов для кодов, которых не бывает.
"""

import re
from pathlib import Path
from typing import Final, get_args

import pytest
from pydantic import BaseModel

from agent.presentation.templates import REQUIRED_ERROR_KEYS, ReplyTemplates, loadTemplates
from common.errors import ErrorCode
from contracts.agent_side import BrainErrorCode, ToolErrorCode

AGENT_SRC: Final = Path(__file__).resolve().parents[2] / "src" / "agent"
# служебный ключ [errors]: текст незнакомого кода
SERVICE_ERROR_KEYS: Final = frozenset({"default"})
POSSIBLE_CODES: Final = frozenset(
    {*ErrorCode, *get_args(BrainErrorCode.__value__), *get_args(ToolErrorCode.__value__)}
)


# Примечание: код агента без самого описания шаблонов — там, где шаблоны читаются
def codeUsingTemplates() -> str:
    files = [p for p in AGENT_SRC.rglob("*.py") if p.name != "templates.py"]
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


# Примечание: (секция, поле) всех строковых шаблонов, кроме словаря [errors]
def templateFields() -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for section, info in ReplyTemplates.model_fields.items():
        annotation = info.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            fields.extend((section, name) for name in annotation.model_fields)
    return fields


# Примечание: любой код, который может дойти до пользователя, — со своим текстом, не «default»
@pytest.mark.parametrize("code", sorted(POSSIBLE_CODES))
def test_everyPossibleCodeHasText(code: str) -> None:
    errors = loadTemplates().errors
    assert code in errors, f"нет текста ошибки {code}"
    assert errors[code] != errors["default"]


# Примечание: требование при старте агента покрывает все коды стандарта
def test_startupCheckCoversStandard() -> None:
    assert {str(code) for code in ErrorCode} <= REQUIRED_ERROR_KEYS


# Примечание: в [errors] нет лишних кодов (опечатка или устаревший код)
def test_noStaleErrorTexts() -> None:
    extra = set(loadTemplates().errors) - SERVICE_ERROR_KEYS - {str(c) for c in POSSIBLE_CODES}
    assert extra == set()


# Примечание: каждый шаблон читается кодом агента (обращение .поле), иначе — мёртвый текст
@pytest.mark.parametrize(("section", "field"), templateFields())
def test_everyTemplateUsed(section: str, field: str) -> None:
    assert re.search(rf"\.{field}\b", codeUsingTemplates()), f"[{section}] {field} не используется"
