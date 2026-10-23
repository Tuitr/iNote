"""Выгрузка OpenAPI в contracts/openapi/api.json: по нему E генерирует типы и моки.

Запуск из корня репозитория: python -m services.api.export_openapi
"""

import json
from pathlib import Path

from services.api.app import app

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "contracts" / "openapi" / "api.json"


def render_openapi() -> str:
    return json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"


def main() -> None:
    # newline="\n": иначе на Windows файл запишется с \r\n и разойдётся с Linux и CI.
    OPENAPI_PATH.write_text(render_openapi(), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
