"""Таблицы переменных окружения в README бота и агента — из классов настроек (S4, шаг 6).

Источник — `BotSettings`, `AgentSettings` и общие поля `BaseServiceSettings` (description полей):
таблица не расходится с кодом. Блок в README — между `<!-- env:begin -->` и `<!-- env:end -->`.

    uv run python deploy/env_docs.py          # переписать блоки
    uv run python deploy/env_docs.py --check  # выход 1, если README устарел (так делает тест)
"""

import sys
from pathlib import Path
from typing import Final

from pydantic import SecretStr
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from agent.infrastructure.settings import AgentSettings
from bot.infrastructure.settings import BotSettings
from common.config import BaseServiceSettings

ROOT: Final = Path(__file__).resolve().parents[1]
BEGIN: Final = "<!-- env:begin -->"
END: Final = "<!-- env:end -->"
TARGETS: Final[dict[Path, type[BaseServiceSettings]]] = {
    ROOT / "services" / "bot" / "README.md": BotSettings,
    ROOT / "services" / "agent" / "README.md": AgentSettings,
}
SHARED: Final = frozenset(BaseServiceSettings.model_fields)


# Примечание: значение по умолчанию для таблицы: секрет и пустое — «—», обязательное — «обязательно»
def defaultText(info: FieldInfo) -> str:
    default = info.default
    if default is PydanticUndefined:
        return "обязательно"
    if default is None or isinstance(default, SecretStr) or default == "":
        return "—"
    value = getattr(default, "value", default)
    return f"`{value}`"


# Примечание: markdown-таблица: сначала переменные сервиса, затем общие
def renderTable(settings: type[BaseServiceSettings]) -> str:
    rows = ["| Переменная | По умолчанию | Зачем |", "|---|---|---|"]
    own = [(n, i) for n, i in settings.model_fields.items() if n not in SHARED]
    shared = [(n, i) for n, i in settings.model_fields.items() if n in SHARED]
    for name, info in [*own, *shared]:
        alias = info.validation_alias
        note = info.description or ""
        if name in SHARED:
            note = f"общая: {note}"
        rows.append(f"| `{alias}` | {defaultText(info)} | {note} |")
    return "\n".join(rows)


# Примечание: README с новым блоком таблицы; блока нет — ошибка (маркеры ставятся руками)
def withTable(readme: str, table: str) -> str:
    if BEGIN not in readme or END not in readme:
        msg = f"в README нет маркеров {BEGIN} … {END}"
        raise ValueError(msg)
    head, rest = readme.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{BEGIN}\n{table}\n{END}{tail}"


# Примечание: переписать блоки или (check) только сравнить; код выхода — устарели ли README
def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale: list[str] = []
    for path, settings in TARGETS.items():
        current = path.read_text(encoding="utf-8")
        wanted = withTable(current, renderTable(settings))
        if wanted != current:
            stale.append(str(path.relative_to(ROOT)))
            if not check:
                path.write_text(wanted, encoding="utf-8")
    if check and stale:
        print("таблица переменных устарела:", ", ".join(stale))  # noqa: T201
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
