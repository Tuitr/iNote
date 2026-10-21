"""Правила хода, которые держит C, а не Brain (CLAUDE.md, правило 3)."""

import uuid
from collections.abc import Iterable, Mapping
from typing import Final

from contracts.agent_side import ExecutedAction, ItemBrief, TurnResult

# Изменяющие инструменты J3: подтверждение «сделано» возможно только после их успешного вызова
MUTATING_TOOLS: Final = frozenset(
    {"create_task", "create_event", "update_item", "complete_item", "delete_item"}
)
NO_ACTION_ERROR: Final = "llm_no_action"


# Примечание: успешные изменяющие вызовы — только они подтверждают «записал»
def successfulMutations(actions: Iterable[ExecutedAction]) -> list[ExecutedAction]:
    return [a for a in actions if a.name in MUTATING_TOOLS and a.result.ok]


# Примечание: done без успешного изменения → error llm_no_action (модель «сделала» без вызова)
def enforceActionRule(result: TurnResult) -> TurnResult:
    if result.outcome != "done" or successfulMutations(result.actions):
        return result
    return result.model_copy(
        update={"outcome": "error", "errorCode": NO_ACTION_ERROR, "clarify": None, "say": None}
    )


# Примечание: краткие записи из данных инструментов (item, items, results[].item) — для фокуса
def briefsFromActions(actions: Iterable[ExecutedAction]) -> dict[uuid.UUID, ItemBrief]:
    found: dict[uuid.UUID, ItemBrief] = {}
    for action in actions:
        data = action.result.data or {}
        for raw in _itemsIn(data):
            try:
                brief = ItemBrief.fromWire(raw)
            except ValueError:
                continue
            found[brief.id] = brief
    return found


# Примечание: все словари записей в structuredContent по формам J3 из плана
def _itemsIn(data: Mapping[str, object]) -> list[object]:
    items: list[object] = []
    if isinstance(item := data.get("item"), dict):
        items.append(item)
    if isinstance(listed := data.get("items"), list):
        items.extend(listed)
    if isinstance(results := data.get("results"), list):
        items.extend(r["item"] for r in results if isinstance(r, dict) and "item" in r)
    return items
