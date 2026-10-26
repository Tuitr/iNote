"""Доступ к contracts/examples из тестов контрактов."""

import json
from pathlib import Path
from typing import Final

EXAMPLES_DIR: Final = Path(__file__).resolve().parents[3] / "contracts" / "examples"


# Примечание: JSON примера по пути относительно contracts/examples
def loadExample(relative: str) -> object:
    data: object = json.loads((EXAMPLES_DIR / relative).read_text(encoding="utf-8"))
    return data
