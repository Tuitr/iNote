"""Каталог /v1/actions ↔ примеры (S4, к заморозке G4): у каждого действия есть пример в
contracts/examples/actions, пример — валидный ActionRequest, и агент принимает его по каталогу
(не «Не понял кнопку»).
"""

import json
from pathlib import Path
from typing import Final

import pytest

from agent.application import actions
from contracts.reply import ActionRequest
from services.agent.tests.fakes import mutation
from services.agent.tests.unit.test_handle_action import TEMPLATES, Rig

EXAMPLES: Final = Path(__file__).resolve().parents[4] / "contracts" / "examples" / "actions"
# имена действий каталога — строковые константы actions.py без суффикса _ARG и значений args
CATALOG: Final = frozenset(
    value
    for name, value in vars(actions).items()
    if name.isupper() and isinstance(value, str) and not name.endswith("_ARG")
)


# Примечание: примеры по файлам
def examples() -> dict[str, ActionRequest]:
    return {
        path.stem: ActionRequest.fromJson(path.read_text(encoding="utf-8"))
        for path in sorted(EXAMPLES.glob("*.json"))
    }


# Примечание: у каждого действия каталога — пример, у каждого примера — действие из каталога
def test_everyActionHasExample() -> None:
    covered = {request.action for request in examples().values()}
    assert covered == CATALOG


# Примечание: пример принимается каталогом агента — ответ не «Не понял кнопку»
@pytest.mark.parametrize("name", sorted(examples()))
async def test_exampleAccepted(name: str) -> None:
    request = examples()[name]
    responses = {
        tool: mutation(status)
        for tool, status in [
            ("delete_item", "deleted"),
            ("update_item", "updated"),
            ("complete_item", "completed"),
        ]
    }
    reply = await Rig(responses=responses).press(
        request.action, dict(request.args), label=request.label
    )
    assert reply.text != TEMPLATES.notices.unknownAction, name


# Примечание: пример сериализуется обратно без потерь (snake_case на проводе)
@pytest.mark.parametrize("name", sorted(examples()))
def test_exampleRoundTrip(name: str) -> None:
    raw = json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    assert examples()[name].toWire() == raw
