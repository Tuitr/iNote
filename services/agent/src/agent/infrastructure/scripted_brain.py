"""ScriptedBrain — правила вместо LLM (C): реализация Brain из J2 для моков и e2e, без сети.

Понимает только: «что у меня сегодня / завтра / на неделе», «напомни … (завтра в 10)»,
«создай задачу …», «встреча с … завтра в 15», «найди …», «перенеси на завтра в 15»
(запись в фокусе — после кнопки «Изменить время» или только что созданная).
Всё остальное — outcome=answer с подсказкой.
Уточняет (S2): время у «напомни» и встречи без времени; «правильно понял?» при неуверенном ASR;
свободный слот при конфликте встреч. pending — свой формат (kind: time | confirm | slot),
агент его не разбирает. Работает только через ToolExecutor, actions — из ToolResult (правило 3).
"""

import re
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Final, Literal, cast, get_args
from zoneinfo import ZoneInfo

from agent.application.item_time import moveArgs, timeField
from agent.domain.periods import Period, periodRange
from contracts.agent_side import (
    Clarify,
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
EMPTY_TITLE: Final = "Не понял, что записать: например, «создай задачу купить хлеб»."
ASK_TIME: Final = "На какое время? Выбери вариант или напиши, например: «завтра в 10»."
ASK_CONFIRM: Final = "Я правильно понял: «{text}»?"
CONFIRM_OPTIONS: Final = ("Да", "Нет, напишу текстом")
WRITE_TEXT: Final = "Хорошо, напиши текстом."
ASK_SLOT: Final = "В это время уже есть: {busy}. Выбери свободное время:"
NO_SLOTS: Final = "В это время уже есть: {busy}, а свободных окон рядом нет. Назови другое время."
BAD_CHOICE: Final = "Не понял выбор — повтори просьбу."
# Варианты времени по таблице толкования: утро, день, вечер
TIME_OPTIONS: Final = (9, 13, 19)

type DayWord = Literal["today", "tomorrow"]

_LIST_RE: Final = re.compile(
    r"^(?:что\s+у\s+меня|что\s+запланировано|покажи(?:\s+дела)?)\s+"
    r"(?:на\s+)?(?P<period>сегодня|завтра|(?:этой\s+)?неделе|неделю)$",
    re.IGNORECASE,
)
_REMIND_RE: Final = re.compile(r"^напомни(?:\s+мне)?\s+(?P<rest>.+)$", re.IGNORECASE)
_TASK_RE: Final = re.compile(r"^(?:создай|добавь|запиши)\s+задачу\s*(?P<rest>.*)$", re.IGNORECASE)
_SEARCH_RE: Final = re.compile(r"^(?:найди|поищи)\s+(?P<rest>.+)$", re.IGNORECASE)
_EVENT_RE: Final = re.compile(r"^(?P<word>встреча|созвон)\s+(?P<rest>.+)$", re.IGNORECASE)
# Ответ на вопрос о времени текстом: «в 15», «15:30», «завтра в 10»
_TIME_ANSWER_RE: Final = re.compile(
    r"^(?:(?P<day>сегодня|завтра)\s+)?(?:в\s+)?(?P<hour>[01]?\d|2[0-3])(?::(?P<minute>[0-5]\d))?$",
    re.IGNORECASE,
)
# «перенеси (на) завтра в 15» — перенос записи в фокусе
_RESCHEDULE_RE: Final = re.compile(r"^(?:перенеси|передвинь)(?:\s+на)?\s+", re.IGNORECASE)
_DAY_RE: Final = re.compile(r"\b(?P<day>сегодня|завтра)\b", re.IGNORECASE)
# Только простое время: «сегодня/завтра в 10» или «в 10:30»
_WHEN_RE: Final = re.compile(
    r"\b(?P<day>сегодня|завтра)\s+в\s+(?P<hour>[01]?\d|2[0-3])(?::(?P<minute>[0-5]\d))?\b",
    re.IGNORECASE,
)
_TOOL_ERRORS: Final = frozenset(get_args(ToolErrorCode.__value__))


# Примечание: фраза без лишних пробелов и знаков в конце; регистр сохраняется — он нужен в названии
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" .!?…")


# Примечание: Brain на правилах: разбор фраз, уточнения и resume по кнопке
class ScriptedBrain:
    # Примечание: разбор фразы → один вызов инструмента, уточнение или ответ без инструментов
    async def run(self, turn: TurnInput, tools: ToolExecutor) -> TurnResult:
        started = time.perf_counter()
        text = normalize(turn.text)
        if turn.asrLowConfidence:
            return _clarify(
                ASK_CONFIRM.format(text=text),
                ["confirmation"],
                list(CONFIRM_OPTIONS),
                {"kind": "confirm", "text": turn.text},
                started=started,
            )
        pending = turn.pending
        if pending and pending.get("kind") == "time":
            if (when := _timeAnswer(turn, text)) is not None:
                return await self._completeWithTime(turn, tools, pending, when, started)
        elif not pending and len(turn.focus) == 1:
            # «На когда перенести?» (кнопка агента) или «перенеси на завтра в 15» — запись в фокусе
            moved = _RESCHEDULE_RE.sub("", text)
            if (when := _timeAnswer(turn, moved)) is not None:
                focused = turn.focus[0]
                args = moveArgs(str(focused.id), focused.kind, when)
                return await self._call(tools, "update_item", args, "done", started)
        return await self._parse(turn, tools, text, started)

    # Примечание: выбор варианта по кнопке; choice — номер варианта из clarify.options
    async def resume(self, turn: TurnInput, choice: str, tools: ToolExecutor) -> TurnResult:
        started = time.perf_counter()
        pending = turn.pending or {}
        index = int(choice) if choice.isdigit() else -1
        kind = pending.get("kind")
        if kind == "confirm":
            if index != 0:
                return _answer(WRITE_TEXT, started)
            confirmed = turn.model_copy(
                update={"text": str(pending.get("text") or turn.text), "asrLowConfidence": False}
            )
            return await self._parse(confirmed, tools, normalize(confirmed.text), started)
        options = _strList(pending.get("options"))
        if kind == "time" and 0 <= index < len(options):
            when = datetime.fromisoformat(options[index])
            return await self._completeWithTime(turn, tools, pending, when, started)
        slots = _slotList(pending.get("slots"))
        if kind == "slot" and 0 <= index < len(slots):
            args = {**_asArgs(pending.get("args")), **slots[index]}
            return await self._createEvent(turn, tools, args, started)
        return _answer(BAD_CHOICE, started)

    # Примечание: обычная фраза: список, напоминание, задача, встреча, поиск или подсказка
    async def _parse(
        self, turn: TurnInput, tools: ToolExecutor, text: str, started: float
    ) -> TurnResult:
        if listed := _LIST_RE.match(text):
            period = _period(listed["period"])
            span = periodRange(period, turn.now, turn.tz)
            # J3 у D: from и to — даты в поясе пользователя, to не включается
            args: dict[str, object] = {
                "from": span.start.date().isoformat(),
                "to": span.end.date().isoformat(),
            }
            return await self._call(tools, "list_items", args, "answer", started)
        if reminded := _REMIND_RE.match(text):
            return await self._create(turn, tools, reminded["rest"], "reminder", started)
        if tasked := _TASK_RE.match(text):
            return await self._create(turn, tools, tasked["rest"], "task", started)
        if event := _EVENT_RE.match(text):
            return await self._event(turn, tools, event["word"], event["rest"], started)
        if searched := _SEARCH_RE.match(text):
            args = {"query": searched["rest"], "limit": SEARCH_LIMIT}
            return await self._call(tools, "search_items", args, "answer", started)
        return _answer(HELP, started)

    # Примечание: напоминание без времени — вопрос с вариантами; задача без времени — сразу
    async def _create(
        self,
        turn: TurnInput,
        tools: ToolExecutor,
        rest: str,
        kind: Literal["task", "reminder"],
        started: float,
    ) -> TurnResult:
        when, title = _extractWhen(rest, turn.now, turn.tz)
        day, title = _extractDay(title) if when is None else (None, title)
        if not title:
            return _answer(EMPTY_TITLE, started)
        args: dict[str, object] = {"title": title[0].upper() + title[1:], "kind": kind}
        if kind == "reminder" and when is None:
            return self._askTime(
                turn, tool="create_task", args=args, field="remind_at", day=day, started=started
            )
        if when is not None:
            args.update(_taskTime(kind, when))
        return await self._call(tools, "create_task", args, "done", started)

    # Примечание: «встреча с Петром завтра в 15» → create_event; без времени — вопрос
    async def _event(
        self, turn: TurnInput, tools: ToolExecutor, word: str, rest: str, started: float
    ) -> TurnResult:
        when, title = _extractWhen(rest, turn.now, turn.tz)
        day, title = _extractDay(title) if when is None else (None, title)
        if not title:
            return _answer(EMPTY_TITLE, started)
        args: dict[str, object] = {"title": f"{word.capitalize()} {title}"}
        if when is None:
            return self._askTime(
                turn, tool="create_event", args=args, field="starts_at", day=day, started=started
            )
        return await self._createEvent(
            turn, tools, {**args, "starts_at": when.isoformat()}, started
        )

    # Примечание: create_event; conflict — не ошибка: вопрос со свободными слотами из ответа
    async def _createEvent(
        self, turn: TurnInput, tools: ToolExecutor, args: Mapping[str, object], started: float
    ) -> TurnResult:
        result = await self._call(tools, "create_event", args, "done", started)
        if result.outcome != "done":
            return result
        data = result.actions[-1].result.data or {}
        if data.get("status") != "conflict":
            return result
        busy = ", ".join(f"«{t}»" for t in _conflictTitles(data)) or "другая встреча"
        slots = [
            slot for raw in _asList(data.get("free_slots")) if (slot := _slot(raw)) is not None
        ]
        if not slots:
            return _answer(NO_SLOTS.format(busy=busy), started, actions=result.actions)
        labels = [_slotLabel(slot, turn.tz) for slot in slots]
        pending: dict[str, object] = {
            "kind": "slot",
            "args": {k: v for k, v in args.items() if k not in {"starts_at", "ends_at"}},
            "slots": [dict(slot) for slot in slots],
        }
        return _clarify(
            ASK_SLOT.format(busy=busy),
            ["starts_at"],
            labels,
            pending,
            started=started,
            actions=result.actions,
        )

    # Примечание: вопрос о времени: варианты 9:00 / 13:00 / 19:00 — ближайшие в будущем
    def _askTime(
        self,
        turn: TurnInput,
        *,
        tool: str,
        args: Mapping[str, object],
        field: str,
        day: DayWord | None,
        started: float,
    ) -> TurnResult:
        moments = _timeOptions(turn.now, turn.tz, day)
        pending: dict[str, object] = {
            "kind": "time",
            "tool": tool,
            "args": dict(args),
            "field": field,
            "options": [m.isoformat() for m in moments],
        }
        labels = [_momentLabel(m, turn.now, turn.tz) for m in moments]
        return _clarify(ASK_TIME, [field], labels, pending, started=started)

    # Примечание: время выбрано — дособираем вызов из pending и выполняем
    async def _completeWithTime(
        self,
        turn: TurnInput,
        tools: ToolExecutor,
        pending: Mapping[str, object],
        when: datetime,
        started: float,
    ) -> TurnResult:
        args = _asArgs(pending.get("args"))
        if pending.get("tool") == "create_event":
            return await self._createEvent(
                turn, tools, {**args, "starts_at": when.isoformat()}, started
            )
        kind: Literal["task", "reminder"] = "task" if args.get("kind") == "task" else "reminder"
        return await self._call(
            tools, "create_task", {**args, **_taskTime(kind, when)}, "done", started
        )

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


# Примечание: ответ временем: «в 15», «15:30», «завтра в 10»; без дня и уже прошло — завтра
def _timeAnswer(turn: TurnInput, text: str) -> datetime | None:
    match = _TIME_ANSWER_RE.match(text)
    if match is None:
        return None
    zone = ZoneInfo(turn.tz)
    local = turn.now.astimezone(zone)
    hour, minute = int(match["hour"]), int(match["minute"] or 0)
    day = local.date() + timedelta(days=1 if (match["day"] or "").lower() == "завтра" else 0)
    moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
    if match["day"] is None and moment <= local:
        moment += timedelta(days=1)
    return moment


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


# Примечание: ответ без изменений; actions — если инструмент уже вызывали (конфликт без окон)
def _answer(say: str, started: float, *, actions: Sequence[ExecutedAction] = ()) -> TurnResult:
    return TurnResult(outcome="answer", say=say, actions=list(actions), usage=_usage(started))


# Примечание: уточнение: вопрос, варианты кнопками и свой pending (агент его не разбирает)
def _clarify(
    say: str,
    missing: list[str],
    options: list[str],
    pending: dict[str, object],
    *,
    started: float,
    actions: Sequence[ExecutedAction] = (),
) -> TurnResult:
    return TurnResult(
        outcome="clarify",
        say=say,
        clarify=Clarify(missing=missing, options=options, pending=pending),
        actions=list(actions),
        usage=_usage(started),
    )


# Примечание: время записи по полю D: у напоминания — remind_at, у задачи — starts_at (срок)
def _taskTime(kind: Literal["task", "reminder"], when: datetime) -> dict[str, object]:
    return {timeField(kind): when.isoformat()}


# Примечание: «завтра» без времени — запомнить день и убрать слово из названия
def _extractDay(rest: str) -> tuple[DayWord | None, str]:
    match = _DAY_RE.search(rest)
    if match is None:
        return None, rest.strip()
    title = re.sub(r"\s+", " ", rest[: match.start()] + rest[match.end() :]).strip(" ,")
    return ("tomorrow" if match["day"].lower() == "завтра" else "today"), title


# Примечание: 9:00, 13:00, 19:00 в поясе пользователя: в названный день или ближайшие в будущем
def _timeOptions(now: datetime, tz: str, day: DayWord | None) -> list[datetime]:
    zone = ZoneInfo(tz)
    local = now.astimezone(zone)
    today = local.date()
    if day is not None:
        base = today + timedelta(days=1 if day == "tomorrow" else 0)
        moments = [datetime(base.year, base.month, base.day, h, tzinfo=zone) for h in TIME_OPTIONS]
        return [m for m in moments if m > local] or [m + timedelta(days=1) for m in moments]
    options: list[datetime] = []
    for hour in TIME_OPTIONS:
        moment = datetime(today.year, today.month, today.day, hour, tzinfo=zone)
        options.append(moment if moment > local else moment + timedelta(days=1))
    return sorted(options)


# Примечание: подпись варианта: «сегодня 19:00» / «завтра 9:00» / «пт 23.10 9:00»
def _momentLabel(moment: datetime, now: datetime, tz: str) -> str:
    zone = ZoneInfo(tz)
    days = (moment.astimezone(zone).date() - now.astimezone(zone).date()).days
    local = moment.astimezone(zone)
    day = {0: "сегодня", 1: "завтра"}.get(days, f"{local:%d.%m}")
    return f"{day} {local.hour}:{local:%M}"


# Примечание: свободное окно {starts_at, ends_at} из ответа инструмента; не по форме — None
def _slot(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    start, end = raw.get("starts_at"), raw.get("ends_at")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    return {"starts_at": start, "ends_at": end}


# Примечание: подпись слота «16:00–17:00» в поясе пользователя
def _slotLabel(slot: Mapping[str, str], tz: str) -> str:
    zone = ZoneInfo(tz)
    start = datetime.fromisoformat(slot["starts_at"]).astimezone(zone)
    end = datetime.fromisoformat(slot["ends_at"]).astimezone(zone)
    return f"{start:%H:%M}–{end:%H:%M}"


# Примечание: названия записей, с которыми конфликт
def _conflictTitles(data: Mapping[str, object]) -> list[str]:
    return [
        str(item["title"])
        for item in _asList(data.get("conflicts"))
        if isinstance(item, dict) and item.get("title")
    ]


# Примечание: list из JSON инструмента или pending
def _asList(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


# Примечание: строки из pending (варианты времени)
def _strList(value: object) -> list[str]:
    return [str(v) for v in _asList(value) if isinstance(v, str)]


# Примечание: слоты из pending
def _slotList(value: object) -> list[dict[str, str]]:
    return [slot for raw in _asList(value) if (slot := _slot(raw)) is not None]


# Примечание: аргументы вызова из pending (копия, без caller — его ставит агент)
def _asArgs(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if k != "caller"}


# Примечание: расход хода — LLM не вызывалась
def _usage(started: float) -> Usage:
    latencyMs = round((time.perf_counter() - started) * 1000)
    return Usage(model=MODEL_NAME, llmCalls=0, tokensIn=0, tokensOut=0, latencyMs=latencyMs)
