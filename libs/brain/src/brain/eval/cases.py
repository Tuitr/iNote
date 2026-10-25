"""Кейсы eval из YAML и сверка хода с ожиданием.

Кейс: фраза, «сейчас», пояс, фокус, записи «в базе» и ожидание — outcome и вызовы инструментов
с аргументами. Аргумент сверяется значением или правилом: {contains: [...]} — подстроки без учёта
регистра, {prefix: ...} — начало строки (короткий id), {any_of: [...]} — любое из,
{absent: true} — поле не передано. Время с T сверяется как момент: смещение может быть любым.
Лишние чтения (search_items, list_items) допускаются, лишние изменения — нет.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Final, cast

import yaml  # type: ignore[import-untyped]

from contracts.agent_side import ExecutedAction, TurnResult

MUTATING_TOOLS: Final = frozenset(
    {"create_task", "create_event", "update_item", "complete_item", "delete_item"}
)
_MISSING: Final = object()

type Json = object


# Примечание: ожидаемый вызов: имя инструмента и правила для аргументов
@dataclass(frozen=True)
class ExpectedCall:
    name: str
    args: Mapping[str, Json]


# Примечание: кейс eval; items — записи, которые фейковые инструменты «находят» в базе
@dataclass(frozen=True)
class EvalCase:
    id: str
    group: str
    text: str
    now: str
    tz: str
    outcome: Json
    calls: Sequence[ExpectedCall]
    focus: Sequence[Mapping[str, Json]] = ()
    items: Sequence[Mapping[str, Json]] = ()
    conflict: bool = False


# Примечание: итог сверки одного прогона: прошёл ли и почему нет
@dataclass(frozen=True)
class Verdict:
    passed: bool
    reasons: list[str] = field(default_factory=list)


# Примечание: файл кейсов: defaults (now, tz) и список cases; даты YAML — снова строки ISO
def loadCases(path: Path) -> list[EvalCase]:
    raw = _plain(yaml.safe_load(path.read_text(encoding="utf-8")))
    if not isinstance(raw, dict):
        msg = f"{path}: ожидается объект с defaults и cases"
        raise TypeError(msg)
    defaults = cast("dict[str, Json]", raw.get("defaults") or {})
    cases = [_case({**defaults, **item}) for item in cast("list[dict[str, Json]]", raw["cases"])]
    ids = [case.id for case in cases]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        msg = f"{path}: повторяются id кейсов: {duplicates}"
        raise ValueError(msg)
    return cases


# Примечание: ход сверяется с ожиданием: outcome, затем вызовы по порядку
def judge(case: EvalCase, result: TurnResult) -> Verdict:
    reasons: list[str] = []
    if not matches(case.outcome, result.outcome):
        reasons.append(f"outcome {result.outcome}, ожидался {case.outcome}")
    reasons.extend(_callReasons(case.calls, result.actions))
    return Verdict(passed=not reasons, reasons=reasons)


# Примечание: значение или правило против фактического значения (_MISSING — поле не передано)
def matches(expected: Json, actual: Json) -> bool:
    if isinstance(expected, dict):
        return _matchesRule(expected, actual)
    if actual is _MISSING:
        return False
    if isinstance(expected, str) and isinstance(actual, str) and "T" in expected:
        return _sameMoment(expected, actual)
    return expected == actual


def _matchesRule(rule: Mapping[str, Json], actual: Json) -> bool:
    if "absent" in rule:
        return actual is _MISSING
    if "any_of" in rule:
        return any(matches(option, actual) for option in _items(rule["any_of"]))
    if "contains" in rule:
        text = actual.lower() if isinstance(actual, str) else ""
        return all(str(word).lower() in text for word in _items(rule["contains"]))
    if "prefix" in rule:
        return isinstance(actual, str) and actual.lower().startswith(str(rule["prefix"]))
    msg = f"неизвестное правило сверки: {dict(rule)}"
    raise ValueError(msg)


def _callReasons(expected: Sequence[ExpectedCall], actions: Sequence[ExecutedAction]) -> list[str]:
    position = 0
    for want in expected:
        while position < len(actions) and not _callMatches(want, actions[position]):
            if actions[position].name in MUTATING_TOOLS:
                return [f"лишний вызов {actions[position].name} {actions[position].args}"]
            position += 1
        if position == len(actions):
            return [f"нет вызова {want.name} {dict(want.args)}; были {_names(actions)}"]
        position += 1
    extra = [a for a in actions[position:] if a.name in MUTATING_TOOLS]
    return [f"лишний вызов {a.name} {a.args}" for a in extra]


def _callMatches(want: ExpectedCall, action: ExecutedAction) -> bool:
    return action.name == want.name and all(
        matches(rule, action.args.get(key, _MISSING)) for key, rule in want.args.items()
    )


def _sameMoment(expected: str, actual: str) -> bool:
    try:
        left, right = datetime.fromisoformat(expected), datetime.fromisoformat(actual)
    except ValueError:
        return False
    if left.tzinfo is None or right.tzinfo is None:
        return False
    return left == right


def _case(raw: Mapping[str, Json]) -> EvalCase:
    expect = cast("dict[str, Json]", raw.get("expect") or {})
    calls = [
        ExpectedCall(name=str(c["name"]), args=cast("dict[str, Json]", c.get("args") or {}))
        for c in cast("list[dict[str, Json]]", expect.get("calls") or [])
    ]
    return EvalCase(
        id=str(raw["id"]),
        group=str(raw["group"]),
        text=str(raw["text"]),
        now=str(raw["now"]),
        tz=str(raw["tz"]),
        outcome=expect.get("outcome", "done"),
        calls=calls,
        focus=cast("list[dict[str, Json]]", raw.get("focus") or []),
        items=cast("list[dict[str, Json]]", raw.get("items") or []),
        conflict=bool(raw.get("conflict", False)),
    )


# Примечание: PyYAML превращает 2026-10-25T10:00:00+03:00 в datetime — возвращаем строку ISO
def _plain(value: Json) -> Json:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _items(value: Json) -> list[Json]:
    return list(value) if isinstance(value, list) else [value]


def _names(actions: Sequence[ExecutedAction]) -> list[str]:
    return [a.name for a in actions]
