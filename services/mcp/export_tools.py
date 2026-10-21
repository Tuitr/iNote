"""Снапшот схем инструментов MCP в contracts/mcp/tools.json (J3): ровно то, что отдаёт list_tools.

Файл в репозитории — контракт для C и B: тест краснеет, если схема изменилась, а снапшот нет.
Запуск из корня репозитория: python -m services.mcp.export_tools
"""

import json
from pathlib import Path

from services.mcp.server import list_tool_specs

TOOLS_PATH = Path(__file__).resolve().parents[2] / "contracts" / "mcp" / "tools.json"


def render_tools() -> str:
    tools = [
        tool.model_dump(mode="json", by_alias=True, exclude_none=True) for tool in list_tool_specs()
    ]
    return json.dumps(tools, ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    # newline="\n": иначе на Windows файл запишется с \r\n и разойдётся с Linux и CI.
    TOOLS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOOLS_PATH.write_text(render_tools(), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
