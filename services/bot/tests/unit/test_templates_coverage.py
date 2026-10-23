"""Тексты бота (S4): каждая константа presentation/templates.py используется кодом бота."""

import ast
import re
from pathlib import Path
from typing import Final

import pytest

BOT_SRC: Final = Path(__file__).resolve().parents[2] / "src" / "bot"
TEMPLATES: Final = BOT_SRC / "presentation" / "templates.py"


# Примечание: имена констант UPPER_SNAKE из модуля текстов
def constants() -> list[str]:
    tree = ast.parse(TEMPLATES.read_text(encoding="utf-8"))
    return [
        node.target.id
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id.isupper()
    ]


# Примечание: константа встречается в коде бота хотя бы раз, кроме своего объявления
@pytest.mark.parametrize("name", constants())
def test_everyTextUsed(name: str) -> None:
    code = "\n".join(p.read_text(encoding="utf-8") for p in BOT_SRC.rglob("*.py"))
    uses = len(re.findall(rf"\b{name}\b", code))
    assert uses >= 2, f"{name} объявлен, но не используется"
