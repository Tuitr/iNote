"""Прогон кейсов eval через Brain: фейковые инструменты, отчёт и трассы JSONL.

Инструменты в eval не ходят в MCP: EvalTools отвечает по записям кейса (создание — новая запись,
поиск и список — записи кейса, конфликт — по флагу кейса). Модель настоящая (GigaChat) или
оракул — фейк, который отвечает ровно ожидаемыми вызовами: им проверяется сам раннер, бесплатно.
"""

import json
import statistics
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final

from brain.eval.cases import EvalCase, Verdict, judge
from brain.llm import FakeLlmClient, FunctionCall, LlmError, LlmReply
from brain.llm_brain import LlmBrain
from brain.prompt import ASK_USER, PROMPT_VERSION
from contracts.agent_side import Brain, ToolResult, ToolSpec, TurnInput, TurnResult, UserSettings

# Четыре обязательных сценария ТЗ: критерий готовности B — точность ≥ 90 % на них
MANDATORY_GROUPS: Final = ("reminder", "event", "list", "search")
TOOL_NAMES: Final = (
    "create_task",
    "create_event",
    "list_items",
    "search_items",
    "update_item",
    "complete_item",
    "delete_item",
    "check_conflicts",
)
SLOT_SHIFT: Final = timedelta(hours=1)

type BrainFactory = Callable[[EvalCase], Awaitable[Brain]]


# Примечание: один прогон кейса: вердикт и итог хода
@dataclass(frozen=True)
class RunRecord:
    case: EvalCase
    repeat: int
    result: TurnResult
    verdict: Verdict


# Примечание: ToolExecutor eval: ответы из записей кейса, вызовы запоминаются для трассы
class EvalTools:
    def __init__(self, case: EvalCase, specs: Sequence[ToolSpec]) -> None:
        self._case = case
        self._specs = list(specs)

    async def listTools(self) -> list[ToolSpec]:
        return self._specs

    async def call(self, name: str, args: Mapping[str, object]) -> ToolResult:
        items = [dict(i) for i in (*self._case.items, *self._case.focus)]
        if name in {"create_task", "create_event"}:
            return self._create(name, args, items)
        if name in {"update_item", "complete_item", "delete_item"}:
            return _change(name, args, items)
        if name == "list_items":
            return _ok(_lines(items), {"items": items, "range": {}, "truncated": False})
        if name == "search_items":
            results = [{"item": item, "score": 0.9} for item in items]
            return _ok(_lines(items), {"results": results, "best_match_confident": bool(items)})
        return _ok("Пересечений нет", {"conflicts": [], "free_slots": []})

    def _create(
        self, name: str, args: Mapping[str, object], items: list[dict[str, object]]
    ) -> ToolResult:
        startsAt = args.get("starts_at")
        if name == "create_event" and self._case.conflict and not args.get("allow_conflict"):
            free = _freeSlot(startsAt)
            return _ok(
                "Не создано: пересекается с другой встречей",
                {"status": "conflict", "item": None, "conflicts": items[:1], "free_slots": free},
            )
        kind = "event" if name == "create_event" else str(args.get("kind") or "task")
        item = {
            "id": str(uuid.uuid4()),
            "kind": kind,
            "title": str(args.get("title") or ""),
            "starts_at": startsAt,
            "remind_at": args.get("remind_at"),
        }
        return _ok(
            f"Создано: [{str(item['id'])[:8]}] {item['title']}", {"status": "created", "item": item}
        )


# Примечание: все кейсы repeat раз; Brain на каждый прогон новый (у оракула свой сценарий)
async def runCases(
    cases: Sequence[EvalCase], makeBrain: BrainFactory, repeat: int
) -> list[RunRecord]:
    specs = [ToolSpec(name=name, description="", inputSchema={}) for name in TOOL_NAMES]
    records: list[RunRecord] = []
    for case in cases:
        for attempt in range(1, repeat + 1):
            brain = await makeBrain(case)
            result = await brain.run(turnInput(case), EvalTools(case, specs))
            records.append(RunRecord(case, attempt, result, judge(case, result)))
    return records


def turnInput(case: EvalCase) -> TurnInput:
    return TurnInput.model_validate(
        {
            "text": case.text,
            "inputType": "text",
            "now": case.now,
            "tz": case.tz,
            "focus": [dict(f) for f in case.focus],
            "settings": UserSettings(tz=case.tz),
        }
    )


# Примечание: оракул — фейковая модель с ожидаемыми вызовами кейса; правила превращаются в значения
def oracleBrain(case: EvalCase) -> LlmBrain:
    script: list[LlmReply | LlmError] = [_reply(c.name, _concrete(c.args)) for c in case.calls]
    if case.outcome == "clarify" and not case.conflict:
        script.append(_reply(ASK_USER, {"question": "Уточни, пожалуйста", "missing": ["details"]}))
    else:
        script.append(LlmReply(text="Вот что получилось", functionCall=None, model="oracle"))
    return LlmBrain(FakeLlmClient(script=script), model="oracle")


# Примечание: отчёт: точность всего, по группам и по обязательным сценариям; задержка и токены
def buildReport(records: Sequence[RunRecord], model: str) -> dict[str, object]:
    byGroup: dict[str, list[bool]] = defaultdict(list)
    for record in records:
        byGroup[record.case.group].append(record.verdict.passed)
    mandatory = [r.verdict.passed for r in records if r.case.group in MANDATORY_GROUPS]
    latencies = sorted(r.result.usage.latencyMs for r in records)
    failed = sorted({r.case.id for r in records if not r.verdict.passed})
    return {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "runs": len(records),
        "accuracy": _share([r.verdict.passed for r in records]),
        "mandatory_accuracy": _share(mandatory),
        "groups": {group: _share(values) for group, values in sorted(byGroup.items())},
        "latency_ms_p50": int(statistics.median(latencies)) if latencies else 0,
        "latency_ms_p95": latencies[int(len(latencies) * 0.95) - 1] if latencies else 0,
        "tokens_in_avg": _mean([r.result.usage.tokensIn for r in records]),
        "tokens_out_avg": _mean([r.result.usage.tokensOut for r in records]),
        "llm_calls_avg": _mean([r.result.usage.llmCalls for r in records]),
        "failed_cases": failed,
    }


# Примечание: трасса JSONL — по строке на прогон: что вызвано, итог, причины провала, расход
def writeTraces(records: Sequence[RunRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for record in records:
            line = {
                "case": record.case.id,
                "group": record.case.group,
                "repeat": record.repeat,
                "passed": record.verdict.passed,
                "reasons": record.verdict.reasons,
                "outcome": record.result.outcome,
                "error_code": record.result.errorCode,
                "say": record.result.say,
                "calls": [{"name": a.name, "args": a.args} for a in record.result.actions],
                "usage": record.result.usage.model_dump(mode="json", by_alias=True),
            }
            file.write(json.dumps(line, ensure_ascii=False) + "\n")


def _change(name: str, args: Mapping[str, object], items: list[dict[str, object]]) -> ToolResult:
    ref = str(args.get("id") or "").lower()
    found = next((i for i in items if str(i["id"]).startswith(ref)), None) if ref else None
    if found is None:
        error = {"code": "not_found", "message": "Запись не найдена"}
        return ToolResult.model_validate(
            {"ok": False, "summary": "Ошибка not_found", "error": error}
        )
    status = {"update_item": "updated", "complete_item": "completed", "delete_item": "deleted"}[
        name
    ]
    changed = {**found, **{k: v for k, v in args.items() if k in found and k != "id"}}
    return _ok(f"Готово: [{ref[:8]}] {found['title']}", {"status": status, "item": changed})


def _freeSlot(startsAt: object) -> list[dict[str, str]]:
    try:
        start = datetime.fromisoformat(str(startsAt)) + SLOT_SHIFT
    except ValueError:
        return []
    return [{"starts_at": start.isoformat(), "ends_at": (start + SLOT_SHIFT).isoformat()}]


def _ok(summary: str, data: dict[str, object]) -> ToolResult:
    return ToolResult(ok=True, summary=summary, data=data)


def _lines(items: Sequence[Mapping[str, object]]) -> str:
    if not items:
        return "Записей нет"
    return "\n".join(f"[{str(i['id'])[:8]}] {i.get('starts_at') or ''} {i['title']}" for i in items)


def _reply(name: str, arguments: dict[str, object]) -> LlmReply:
    return LlmReply(text="", functionCall=FunctionCall(name, arguments), model="oracle")


# Примечание: правило кейса → конкретное значение для оракула (absent — поле не передаётся)
def _concrete(rules: Mapping[str, object]) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, rule in rules.items():
        value = _value(rule)
        if value is not None:
            values[key] = value
    return values


def _value(rule: object) -> object:
    if not isinstance(rule, dict):
        return rule
    if "any_of" in rule and isinstance(rule["any_of"], list):
        return _value(rule["any_of"][0])
    if "contains" in rule and isinstance(rule["contains"], list):
        return " ".join(str(word) for word in rule["contains"])
    if "prefix" in rule:
        return str(rule["prefix"])
    return None


def _share(values: Sequence[bool]) -> float:
    return round(sum(values) / len(values), 3) if values else 0.0


def _mean(values: Sequence[int]) -> float:
    return round(statistics.fmean(values), 1) if values else 0.0
