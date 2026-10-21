"""ScriptedBrain — правила вместо LLM (C): реализация Brain из J2 для моков и e2e, без сети.

Понимает только: «что у меня сегодня / завтра / на неделе», «напомни … (завтра в 10)»,
«создай задачу …», «найди …». Всё остальное — outcome=answer с подсказкой.
Работает только через ToolExecutor, actions — из реальных ToolResult (правило 3).
"""

import re
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final, Literal, cast, get_args
from zoneinfo import ZoneInfo

from contracts.agent_side import (
    ExecutedAction,
    ToolErrorCode,
    ToolExecutor,
    TurnInput,
    TurnResult,
    Usage,
)

MODEL_NAME: Final = "scripted"
SEARCH_LIMIT: Final = 5
HELP: Final = (
    "Пока понимаю только: «что у меня сегодня», «что у меня завтра», «что у меня на неделе», "
    "«напомни завтра в 10 позвонить Ивану», «создай задачу купить хлеб», «найди договор»."
)
ASK_TIME: Final = "Скажи, когда напомнить: например, «напомни завтра в 10 позвонить Ивану»."
EMPTY_TITLE: Final = "Не понял, что записать: например, «создай задачу купить хлеб»."

type Period = Literal["today", "tomorrow", "week"]

_LIST_RE: Final = re.compile(
    r"^(?:что\s+у\s+меня|что\s+запланировано|покажи(?:\s+дела)?)\s+"
    r"(?:на\s+)?(?P<period>сегодня|завтра|(?:этой\s+)?неделе|неделю)$",
    re.IGNORECASE,
)
_REMIND_RE: Final = re.compile(r"^напомни(?:\s+мне)?\s+(?P<rest>.+)$", re.IGNORECASE)
_TASK_RE: Final = re.compile(r"^(?:создай|добавь|запиши)\s+задачу\s*(?P<rest>.*)$", re.IGNORECASE)
_SEARCH_RE: Final = re.compile(r"^(?:найди|поищи)\s+(?P<rest>.+)$", re.IGNORECASE)
# Только простое время: «сегодня/завтра в 10» или «в 10:30»
_WHEN_RE: Final = re.compile(
    r"\b(?P<day>сегодня|завтра)\s+в\s+(?P<hour>[01]?\d|2[0-3])(?::(?P<minute>[0-5]\d))?\b",
    re.IGNORECASE,
)
_TOOL_ERRORS: Final = frozenset(get_args(ToolErrorCode.__value__))


# Примечание: начало и конец периода в поясе пользователя; [from, to) — конец не включается
@dataclass(frozen=True)
class Range:
    start: datetime
    end: datetime


# Примечание: границы «сегодня / завтра / неделя» от now пользователя; неделя — с понедельника
def periodRange(period: Period, now: datetime, tz: str) -> Range:
    zone = ZoneInfo(tz)
    today = now.astimezone(zone).date()
    if period == "today":
        first, days = today, 1
    elif period == "tomorrow":
        first, days = today + timedelta(days=1), 1
    else:
        first, days = today - timedelta(days=today.weekday()), 7
    return Range(start=_midnight(first, zone), end=_midnight(first + timedelta(days=days), zone))


# Примечание: полночь даты в поясе — смещение берётся у пояса на эту дату
def _midnight(day: date, zone: ZoneInfo) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=zone)


# Примечание: фраза без лишних пробелов и знаков в конце; регистр сохраняется — он нужен в названии
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" .!?…")


# Примечание: Brain на правилах; resume — S2
class ScriptedBrain:
    # Примечание: разбор фразы → один вызов инструмента или ответ без инструментов
    async def run(self, turn: TurnInput, tools: ToolExecutor) -> TurnResult:
        started = time.perf_counter()
        text = normalize(turn.text)
        if listed := _LIST_RE.match(text):
            period = _period(listed["period"])
            span = periodRange(period, turn.now, turn.tz)
            args: dict[str, object] = {"from": span.start.isoformat(), "to": span.end.isoformat()}
            return await self._call(tools, "list_items", args, "answer", started)
        if reminded := _REMIND_RE.match(text):
            return await self._create(turn, tools, reminded["rest"], "reminder", started)
        if tasked := _TASK_RE.match(text):
            return await self._create(turn, tools, tasked["rest"], "task", started)
        if searched := _SEARCH_RE.match(text):
            args = {"query": searched["rest"], "limit": SEARCH_LIMIT}
            return await self._call(tools, "search_items", args, "answer", started)
        return _answer(HELP, started)

    # Примечание: уточнения по кнопке варианта появятся в S2
    async def resume(self, turn: TurnInput, choice: str, tools: ToolExecutor) -> TurnResult:
        raise NotImplementedError("ScriptedBrain.resume — S2")

    # Примечание: напоминание без времени не создаём (уточнение — S2), задача без времени — можно
    async def _create(
        self,
        turn: TurnInput,
        tools: ToolExecutor,
        rest: str,
        kind: Literal["task", "reminder"],
        started: float,
    ) -> TurnResult:
        when, title = _extractWhen(rest, turn.now, turn.tz)
        if not title:
            return _answer(EMPTY_TITLE, started)
        if kind == "reminder" and when is None:
            return _answer(ASK_TIME, started)
        args: dict[str, object] = {"title": title[0].upper() + title[1:], "kind": kind}
        if when is not None:
            args["due_at"] = when.isoformat()
            if kind == "reminder":
                args["remind_at"] = when.isoformat()
        return await self._call(tools, "create_task", args, "done", started)

    # Примечание: один вызов; ошибка инструмента → outcome=error с его кодом
    async def _call(
        self,
        tools: ToolExecutor,
        name: str,
        args: Mapping[str, object],
        success: Literal["done", "answer"],
        started: float,
    ) -> TurnResult:
        result = await tools.call(name, args)
        action = ExecutedAction(name=name, args=dict(args), result=result)
        usage = _usage(started)
        if not result.ok:
            code = result.error.code if result.error is not None else "internal"
            # Незнакомый код инструмента сводим к internal — renderer знает только коды стандарта
            errorCode = cast("ToolErrorCode", code if code in _TOOL_ERRORS else "internal")
            return TurnResult(outcome="error", actions=[action], errorCode=errorCode, usage=usage)
        return TurnResult(
            outcome=success, actions=[action], focus=_focusIds(result.data), usage=usage
        )


# Примечание: «сегодня» / «завтра» / «неделе» → период
def _period(word: str) -> Period:
    lowered = word.lower()
    if lowered == "сегодня":
        return "today"
    if lowered == "завтра":
        return "tomorrow"
    return "week"


# Примечание: «завтра в 10» → момент в поясе пользователя и фраза без этого куска
def _extractWhen(rest: str, now: datetime, tz: str) -> tuple[datetime | None, str]:
    match = _WHEN_RE.search(rest)
    if match is None:
        return None, rest.strip()
    zone = ZoneInfo(tz)
    isTomorrow = match["day"].lower() == "завтра"
    day = now.astimezone(zone).date() + timedelta(days=1 if isTomorrow else 0)
    moment = datetime(
        day.year, day.month, day.day, int(match["hour"]), int(match["minute"] or 0), tzinfo=zone
    )
    title = re.sub(r"\s+", " ", (rest[: match.start()] + rest[match.end() :])).strip(" ,")
    return moment, title


# Примечание: id записей из ответа инструмента — в фокус (item, items, results[].item)
def _focusIds(data: Mapping[str, object] | None) -> list[uuid.UUID]:
    if not data:
        return []
    raw: list[object] = []
    if isinstance(item := data.get("item"), dict):
        raw.append(item.get("id"))
    if isinstance(items := data.get("items"), list):
        raw.extend(i.get("id") for i in items if isinstance(i, dict))
    if isinstance(results := data.get("results"), list):
        raw.extend(
            r["item"].get("id")
            for r in results
            if isinstance(r, dict) and isinstance(r.get("item"), dict)
        )
    ids: list[uuid.UUID] = []
    for value in raw:
        try:
            ids.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return ids


# Примечание: ответ без инструментов
def _answer(say: str, started: float) -> TurnResult:
    return TurnResult(outcome="answer", say=say, usage=_usage(started))


# Примечание: расход хода — LLM не вызывалась
def _usage(started: float) -> Usage:
    latencyMs = round((time.perf_counter() - started) * 1000)
    return Usage(model=MODEL_NAME, llmCalls=0, tokensIn=0, tokensOut=0, latencyMs=latencyMs)
