from typing import Final

from agent.application.turn_rules import briefsFromActions, enforceActionRule
from contracts.agent_side import ExecutedAction, ToolResult, TurnResult
from services.agent.tests.fakes import ITEM, USAGE, createdAction

OTHER: Final[dict[str, object]] = {
    **ITEM,
    "id": "1b0c2d4e-6f70-4a8b-9c1d-2e3f4a5b6c7d",
    "title": "Созвон",
}


# Примечание: вызов инструмента с заданными данными
def action(name: str, data: dict[str, object]) -> ExecutedAction:
    return ExecutedAction(name=name, args={}, result=ToolResult(ok=True, summary="", data=data))


# Примечание: записи для фокуса — из item, items и results[].item; мусор пропускается
def test_briefsFromAllShapes() -> None:
    found = briefsFromActions(
        [
            action("list_items", {"items": [ITEM, {"broken": True}], "range": {}}),
            action("search_items", {"results": [{"item": OTHER, "score": 0.9}, {"score": 1}]}),
            action("create_task", {"status": "created", "item": ITEM}),
        ]
    )
    assert {b.title for b in found.values()} == {"Позвонить Ивану", "Созвон"}


# Примечание: answer и успешное done не трогаются
def test_ruleKeepsLegitResults() -> None:
    answer = TurnResult(outcome="answer", say="x", usage=USAGE)
    done = TurnResult(outcome="done", actions=[createdAction()], usage=USAGE)
    assert enforceActionRule(answer) is answer
    assert enforceActionRule(done) is done


# Примечание: list_items не изменяет данных — done на нём одном считается llm_no_action
def test_listIsNotMutation() -> None:
    result = TurnResult(outcome="done", actions=[action("list_items", {"items": []})], usage=USAGE)
    assert enforceActionRule(result).errorCode == "llm_no_action"
