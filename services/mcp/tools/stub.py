"""Примеры ответов из contracts/examples/j3 для инструментов, которые ещё не сделаны (S2).

Инструмент перестаёт отвечать примером, когда появляется в services/mcp/server.py:REAL_TOOLS.
Когда настоящими станут все 8, модуль удаляется.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from libs.contracts.tools import TOOLS

EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "contracts" / "examples" / "j3"


@dataclass(frozen=True)
class ToolExample:
    structured_content: dict[str, Any]
    text: str


def load_examples(examples_dir: Path) -> dict[str, ToolExample]:
    examples = {}
    for name in TOOLS:
        raw = json.loads((examples_dir / f"{name}.json").read_text(encoding="utf-8"))
        examples[name] = ToolExample(structured_content=raw["structuredContent"], text=raw["text"])
    return examples
