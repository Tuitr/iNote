"""Раннер eval на оракуле: кейсы читаются, сверка ловит ошибки модели, отчёт считается."""

import json
from pathlib import Path
from typing import Final

import pytest

from brain.eval.cases import EvalCase, ExpectedCall, judge, loadCases, matches
from brain.eval.runner import (
    MANDATORY_GROUPS,
    EvalTools,
    buildReport,
    oracleBrain,
    runCases,
    writeTraces,
)
from contracts.agent_side import Brain, ExecutedAction, ToolResult, TurnResult, Usage

CASES_PATH: Final = Path(__file__).resolve().parents[4] / "tests" / "eval" / "cases.yaml"
REMIND_AT: Final = "2026-10-25T10:00:00+03:00"
CASE: Final = EvalCase(
    id="reminder",
    group="reminder",
    text="Напомни завтра в 10 позвонить Ивану",
    now="2026-10-24T12:00:00+03:00",
    tz="Europe/Moscow",
    outcome="done",
    calls=[ExpectedCall("create_task", {"remind_at": REMIND_AT, "title": {"contains": ["иван"]}})],
)
USAGE: Final = Usage(model="m", llmCalls=1, tokensIn=10, tokensOut=2, latencyMs=100)


def turnResult(outcome: str, *calls: tuple[str, dict[str, object]]) -> TurnResult:
    created = ToolResult(ok=True, summary="ok", data={"status": "created"})
    actions = [ExecutedAction(name=name, args=args, result=created) for name, args in calls]
    errorCode = "llm_bad_output" if outcome == "error" else None
    return TurnResult.model_validate(
        {"outcome": outcome, "actions": actions, "errorCode": errorCode, "usage": USAGE}
    )


async def oracle(case: EvalCase) -> Brain:
    return oracleBrain(case)


# Примечание: файл кейсов: 30+ кейсов, все обязательные сценарии, id без повторов
def test_casesFile() -> None:
    cases = loadCases(CASES_PATH)
    assert len(cases) >= 30
    assert set(MANDATORY_GROUPS) <= {case.group for case in cases}


# Примечание: оракул отвечает ожидаемыми вызовами — все кейсы проходят: раннер и кейсы согласованы
async def test_oracleRunPassesAll() -> None:
    records = await runCases(loadCases(CASES_PATH), oracle, repeat=1)
    failed = [(r.case.id, r.verdict.reasons) for r in records if not r.verdict.passed]
    assert failed == []


# Примечание: правила сверки: момент с любым смещением, подстроки, префикс, любое из, «не передано»
@pytest.mark.parametrize(
    ("rule", "actual", "expected"),
    [
        (REMIND_AT, "2026-10-25T07:00:00Z", True),
        (REMIND_AT, "2026-10-25T10:00:00", False),
        ({"contains": ["иван"]}, "Позвонить Ивану", True),
        ({"prefix": "1f2e3d4c"}, "1f2e3d4c-0000", True),
        ({"any_of": ["task", {"absent": True}]}, "reminder", False),
        ("2026-10-25", "2026-10-25", True),
    ],
)
def test_matches(rule: object, actual: object, expected: bool) -> None:
    assert matches(rule, actual) is expected


# Примечание: не то время, лишнее изменение, нет вызова, не тот outcome — кейс не прошёл
@pytest.mark.parametrize(
    "result",
    [
        turnResult(
            "done", ("create_task", {"remind_at": "2026-10-25T11:00:00+03:00", "title": "Иван"})
        ),
        turnResult(
            "done",
            ("create_task", {"remind_at": REMIND_AT, "title": "Иван"}),
            ("delete_item", {"id": "1f2e3d4c"}),
        ),
        turnResult("answer", ("search_items", {"query": "иван"})),
        turnResult("error"),
    ],
)
def test_judgeCatchesMistakes(result: TurnResult) -> None:
    assert judge(CASE, result).passed is False


# Примечание: лишнее чтение перед нужным вызовом допустимо
def test_judgeAllowsExtraReads() -> None:
    result = turnResult(
        "done",
        ("search_items", {"query": "иван"}),
        ("create_task", {"remind_at": REMIND_AT, "title": "Позвонить Ивану"}),
    )
    assert judge(CASE, result).passed is True


# Примечание: конфликт по флагу кейса — свободное окно через час; allow_conflict создаёт встречу
async def test_evalToolsConflict() -> None:
    case = EvalCase(**{**CASE.__dict__, "conflict": True})
    tools = EvalTools(case, [])
    starts = {"title": "Встреча", "starts_at": "2026-10-25T15:00:00+03:00"}
    conflict = await tools.call("create_event", starts)
    forced = await tools.call("create_event", {**starts, "allow_conflict": True})
    assert (conflict.data or {})["free_slots"] == [
        {"starts_at": "2026-10-25T16:00:00+03:00", "ends_at": "2026-10-25T17:00:00+03:00"}
    ]
    assert (forced.data or {})["status"] == "created"


# Примечание: отчёт — точность всего и по обязательным сценариям; трасса — строка на прогон
async def test_reportAndTraces(tmp_path: Path) -> None:
    records = await runCases([CASE], oracle, repeat=2)
    report = buildReport(records, "oracle")
    assert (report["runs"], report["accuracy"], report["mandatory_accuracy"]) == (2, 1.0, 1.0)
    traces = tmp_path / "traces.jsonl"
    writeTraces(records, traces)
    lines = [json.loads(line) for line in traces.read_text(encoding="utf-8").splitlines()]
    assert [(line["case"], line["passed"]) for line in lines] == [("reminder", True)] * 2
