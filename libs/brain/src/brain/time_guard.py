"""Проверка момента в вызове модели до MCP: дату и время считает код, неясное — вопрос.

Модель достаёт из фразы что делать; когда — разбирает spoken_time.py, а здесь решается, что
с этим делать перед create_task / create_event / update_item:
- проблема с датой (нет такой даты, противоречие, дата прошла) — вопрос, вызова нет;
- встреча без дня (ни во фразе, ни в вопросе, на который отвечают) — «На какой день?»;
- час 1–12 без «утра / вечера» — «Утра или вечера?» с кнопками, которые сразу выполнят вызов;
- иначе момент из фразы подменяет момент модели: дата — из фразы, время — из фразы или модели;
- время кладётся в поле по виду записи (у напоминания — remind_at, иначе — starts_at): модель
  путала поля, и перенос «без десяти три дня» отвечал «Изменено», ничего не меняя.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from brain.llm import FunctionCall, JsonObject
from brain.spoken_time import SaidTime, Spoken, monthLabel, nearestDate, normalize, parseSpoken
from contracts.agent_side import ItemBrief, ToolResult, TurnInput

TIME_TOOLS: Final = frozenset({"create_task", "create_event", "update_item"})
STARTS_AT: Final = "starts_at"
REMIND_AT: Final = "remind_at"
ENDS_AT: Final = "ends_at"
# свои поля в pending вопросов «На какой день?» и «Во сколько напомнить?»: сказанное раньше
# время или день не теряются, когда пользователь отвечает только на вопрос
SAID_TIME_KEY: Final = "said_time"
SAID_DAY_KEY: Final = "said_day"
ASK_DAY: Final = "На какой день поставить встречу?"
DAY_OPTIONS: Final = ("Сегодня", "Завтра", "Послезавтра")
ASK_HALF_DAY: Final = "Утра или вечера: {first} или {second}?"
ASK_EVENT_TIME: Final = "Во сколько встреча?"
ASK_REMIND_TIME: Final = "Во сколько напомнить?"
TIME_OPTIONS: Final = ("10:00", "15:00", "19:00")
_PART_OF_DAY_RE: Final = re.compile(
    r"утром|утра|днём|днем|вечером|вечера|ночью|ночи|обед|полдень|полночь"
)
# ответ на «утро или вечер?» одним словом
_PART_ANSWER_RE: Final = re.compile(
    r"(?:на\s+|в\s+)?(утро|утра|утром|вечер|вечера|вечером|день|дня|днем|днём|ночь|ночи|ночью)"
)
# начало правки только что созданной записи: «нет, давай на завтра», «лучше в 16»
_CORRECTION_RE: Final = re.compile(
    r"^\s*(?:нет|не|а|давай|лучше|исправь|поменяй|перенеси|передвинь)\b", re.IGNORECASE
)
ASK_MONTH_DAY: Final = "Какого числа в {month}?"
# своё поле pending вопроса «Какого числа в мае?»: месяц, к которому относится ответ числом
SAID_MONTH_KEY: Final = "said_month"
_DAY_NUMBER_RE: Final = re.compile(
    r"(?:на\s+)?(\d{1,2})(?:\s*-?(?:го|е|ое|числа))?(?:\s+числа)?\.?"
)
# своё поле pending: исходная просьба, к которой относится ответ на вопрос
REQUEST_KEY: Final = "request"
SHORT_ID_LENGTH: Final = 8


# Примечание: вместо вызова — вопрос: текст, варианты-кнопки, чего не хватает и pending
@dataclass(frozen=True)
class Ask:
    question: str
    options: list[str]
    missing: list[str]
    pending: JsonObject


# Примечание: вызов с моментом, посчитанным кодом, или вопрос пользователю
def guardTime(call: FunctionCall, turn: TurnInput) -> FunctionCall | Ask:
    if call.name not in TIME_TOOLS:
        return call
    call = _asCorrection(call, turn)
    field = _timeField(call, turn)
    call = _inField(call, field)
    spoken = parseSpoken(turn.text, turn.now, turn.tz)
    if not spoken.supported:
        return call
    pending = turn.pending or {}
    context = _dialogContext(turn)
    day = spoken.day or _monthDayAnswer(pending, turn) or _askedSpoken(pending, turn).day
    day = day or _savedDay(pending) or context.day
    said = spoken.time or _partAnswer(turn.text, _savedTime(pending) or context.time)
    said = said or _savedTime(pending) or context.time
    ask = _question(call, field, spoken, turn, day=day, said=said)
    if ask is not None:
        return _withRequest(ask, turn)
    hasDayContext = bool(pending) or context.day is not None
    return _withMoment(call, field, day, said, turn, nearestModel=not hasDayContext)


# Примечание: кнопкой не нажали, а ответили словом («утро») на «Утра или вечера?» — номер варианта
def partChoice(turn: TurnInput) -> int | None:
    pending = turn.pending or {}
    question = pending.get("question")
    if pending.get("kind") != "call" or not isinstance(question, str):
        return None
    if not question.startswith(ASK_HALF_DAY.split("{", 1)[0]):
        return None
    match = _PART_ANSWER_RE.fullmatch(turn.text.strip().lower().rstrip(".!"))
    if match is None:
        return None
    return 0 if match[1].startswith(("утр", "ноч")) else 1


# Примечание: о чём спросить до вызова: дата, день встречи, придуманное время, утро или вечер
def _question(
    call: FunctionCall,
    field: str,
    spoken: Spoken,
    turn: TurnInput,
    *,
    day: date | None,
    said: SaidTime | None,
) -> Ask | None:
    pending = turn.pending or {}
    if spoken.problem is not None:
        problem = spoken.problem
        asked: JsonObject = {"kind": "ask", "question": problem.question}
        return Ask(problem.question, list(problem.options), [field], asked)
    if spoken.month is not None and day is None and call.name != "update_item":
        question = ASK_MONTH_DAY.format(month=monthLabel(spoken.month))
        monthAsk: JsonObject = {"kind": "ask", "question": question, SAID_MONTH_KEY: spoken.month}
        return Ask(question, [], [field], monthAsk)
    isDated = day is not None or pending.get("kind") == "call" or call.name == "update_item"
    if call.name == "create_event" and not isDated:
        return _askDay(said)
    if said is None and _needsTime(call, field, pending, turn):
        return _askTime(call, day)
    if said is not None and not said.isClear:
        return _askHalfDay(call, field, said, day, turn)
    return None


# Примечание: исходная просьба — в pending вопроса: ответ («01.05», «утро») модель свяжет с ней
def _withRequest(ask: Ask, turn: TurnInput) -> Ask:
    if ask.pending.get("kind") != "ask":
        return ask
    request = (turn.pending or {}).get("request") or turn.text
    return Ask(ask.question, ask.options, ask.missing, {**ask.pending, REQUEST_KEY: request})


# Примечание: «нет, давай на завтра» сразу после создания: модель создавала вторую запись —
# это правка записи из фокуса (того же вида), а не новая
def _asCorrection(call: FunctionCall, turn: TurnInput) -> FunctionCall:
    if call.name == "update_item" or not turn.focus or not _CORRECTION_RE.match(turn.text):
        return call
    kind = "event" if call.name == "create_event" else call.arguments.get("kind", "task")
    brief = next((b for b in reversed(turn.focus) if b.kind == kind), None)
    if brief is None:
        return call
    moments = {k: v for k, v in call.arguments.items() if k in {STARTS_AT, REMIND_AT, ENDS_AT}}
    return FunctionCall("update_item", {"id": str(brief.id), **moments})


# Примечание: идёт уточнение (бот спросил, пользователь отвечает) — день и время из прошлых
# реплик пользователя в этом уточнении: модель спрашивала текстом, и pending не было
def _dialogContext(turn: TurnInput) -> Spoken:
    history = list(turn.history)
    found = Spoken()
    while len(history) >= 2 and history[-1].role == "assistant" and _isQuestion(history[-1].text):  # noqa: PLR2004
        user = history[-2]
        history = history[:-2]
        if user.role != "user":
            break
        spoken = parseSpoken(user.text, turn.now, turn.tz)
        if not spoken.supported or spoken.problem is not None:
            continue
        found = Spoken(day=found.day or spoken.day, time=found.time or spoken.time)
    return found


def _isQuestion(text: str) -> bool:
    return text.rstrip().endswith("?") or "уточни" in text.lower()


# Примечание: ответ «утро» / «вечером» на неясный час — тот же час с частью суток
def _partAnswer(text: str, said: SaidTime | None) -> SaidTime | None:
    match = _PART_ANSWER_RE.fullmatch(text.strip().lower().rstrip(".!"))
    if match is None or said is None or said.isClear:
        return None
    morning, evening = said.variants()
    hour, minute = morning if match[1].startswith(("утр", "ноч")) else evening
    return SaidTime(hour, minute, isClear=True)


# Примечание: подтверждение «Изменено» без изменения: MCP принял, но время записи не то
def timeNotApplied(call: FunctionCall, result: ToolResult) -> bool:
    if call.name != "update_item" or not result.ok:
        return False
    item = (result.data or {}).get("item")
    if not isinstance(item, dict):
        return False
    for field in (STARTS_AT, REMIND_AT):
        asked, saved = _moment(call.arguments.get(field)), _moment(item.get(field))
        if asked is not None and field in item and saved != asked:
            return True
    return False


# Примечание: поле момента по виду записи: напоминание — remind_at, задача и встреча — starts_at
def _timeField(call: FunctionCall, turn: TurnInput) -> str:
    if call.name == "create_event":
        return STARTS_AT
    if call.name == "create_task":
        isReminder = call.arguments.get("kind") == "reminder"
        hasOnlyRemind = REMIND_AT in call.arguments and STARTS_AT not in call.arguments
        return REMIND_AT if isReminder or hasOnlyRemind else STARTS_AT
    brief = _focused(call, turn)
    kind = call.arguments.get("kind") or (brief.kind if brief is not None else None)
    return REMIND_AT if kind == "reminder" else STARTS_AT


# Примечание: перенос: момент из «чужого» поля — в своё (у напоминания starts_at не показывается)
def _inField(call: FunctionCall, field: str) -> FunctionCall:
    if call.name != "update_item":
        return call
    other = REMIND_AT if field == STARTS_AT else STARTS_AT
    arguments = dict(call.arguments)
    if field not in arguments and _moment(arguments.get(other)) is not None:
        arguments[field] = arguments.pop(other)
        return FunctionCall(call.name, arguments)
    # у напоминания starts_at пуст (D): модель дублировала время в оба поля
    if field == REMIND_AT and field in arguments and other in arguments:
        arguments.pop(other)
        return FunctionCall(call.name, arguments)
    return call


# Примечание: запись из фокуса, которую меняет update_item (по полному или короткому id)
def _focused(call: FunctionCall, turn: TurnInput) -> ItemBrief | None:
    itemId = str(call.arguments.get("id") or "").lower()
    if len(itemId) < SHORT_ID_LENGTH:
        return None
    return next((b for b in turn.focus if str(b.id).startswith(itemId)), None)


# Примечание: день из вопроса, на который отвечают («На какое время послезавтра…?»)
def _askedSpoken(pending: Mapping[str, object], turn: TurnInput) -> Spoken:
    question = pending.get("question")
    if not isinstance(question, str):
        return Spoken()
    asked = parseSpoken(question, turn.now, turn.tz)
    return asked if asked.supported and asked.problem is None else Spoken()


# Примечание: день из фразы для pending своего вопроса («напомни послезавтра …» → «Во сколько?»)
def savedDay(turn: TurnInput) -> JsonObject:
    spoken = parseSpoken(turn.text, turn.now, turn.tz)
    return {SAID_DAY_KEY: spoken.day.isoformat()} if spoken.day is not None else {}


# Примечание: ответ числом («4», «4-го», «четыре») на «Какого числа в мае?» — дата в том месяце
def _monthDayAnswer(pending: Mapping[str, object], turn: TurnInput) -> date | None:
    month = pending.get(SAID_MONTH_KEY)
    match = _DAY_NUMBER_RE.fullmatch(normalize(turn.text))
    if not isinstance(month, int) or match is None:
        return None
    today = turn.now.astimezone(ZoneInfo(turn.tz)).date()
    return nearestDate(int(match[1]), month, today)


def _savedDay(pending: Mapping[str, object]) -> date | None:
    raw = pending.get(SAID_DAY_KEY)
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


# Примечание: время, сохранённое в pending вопроса «На какой день?»
def _savedTime(pending: Mapping[str, object]) -> SaidTime | None:
    raw = pending.get(SAID_TIME_KEY)
    if not isinstance(raw, dict):
        return None
    hour, minute, isClear = raw.get("hour"), raw.get("minute"), raw.get("is_clear")
    if not isinstance(hour, int) or not isinstance(minute, int) or not isinstance(isClear, bool):
        return None
    return SaidTime(hour, minute, isClear)


# Примечание: встреча или напоминание с моментом, но время пользователь не называл — модель его
# придумала («послезавтра» → 00:00); «утром», «вечером» без часа — это время (промпт: 09:00, 19:00)
def _needsTime(
    call: FunctionCall, field: str, pending: Mapping[str, object], turn: TurnInput
) -> bool:
    isTimed = call.name == "create_event" or (call.name == "create_task" and field == REMIND_AT)
    if not isTimed or _PART_OF_DAY_RE.search(turn.text.lower()):
        return False
    # ответ на вопрос модели: время могло прозвучать раньше в диалоге — его знает модель
    isOurQuestion = pending.get("question") in {ASK_EVENT_TIME, ASK_REMIND_TIME, ASK_DAY}
    if pending and not isOurQuestion:
        return False
    return _moment(call.arguments.get(field)) is not None


# Примечание: «Во сколько?»: день уже понятен — он сохраняется в pending для ответа временем
def _askTime(call: FunctionCall, day: date | None) -> Ask:
    question = ASK_EVENT_TIME if call.name == "create_event" else ASK_REMIND_TIME
    pending: JsonObject = {"kind": "ask", "question": question}
    if day is not None:
        pending[SAID_DAY_KEY] = day.isoformat()
    field = STARTS_AT if call.name == "create_event" else REMIND_AT
    return Ask(question, list(TIME_OPTIONS), [field], pending)


def _askDay(said: SaidTime | None) -> Ask:
    pending: JsonObject = {"kind": "ask", "question": ASK_DAY}
    if said is not None:
        pending[SAID_TIME_KEY] = {
            "hour": said.hour,
            "minute": said.minute,
            "is_clear": said.isClear,
        }
    return Ask(ASK_DAY, list(DAY_OPTIONS), [STARTS_AT], pending)


# Примечание: «Утра или вечера?»: два момента; кнопка сразу выполнит вызов (pending call)
def _askHalfDay(
    call: FunctionCall, field: str, said: SaidTime, day: date | None, turn: TurnInput
) -> Ask:
    zone = ZoneInfo(turn.tz)
    now = turn.now.astimezone(zone)
    base = day or _modelDay(call, field, turn)
    moments = [
        _combine(base, hour, minute, zone) if base else _nearest(hour, minute, now, zone)
        for hour, minute in said.variants()
    ]
    labels = [f"{m:%H:%M}" for m in moments]
    args = {k: v for k, v in call.arguments.items() if k not in {field, ENDS_AT}}
    question = ASK_HALF_DAY.format(first=labels[0], second=labels[1])
    pending: JsonObject = {
        "kind": "call",
        "tool": call.name,
        "args": args,
        "choices": [{field: m.isoformat()} for m in moments],
        "question": question,
    }
    return Ask(question, labels, [field], pending)


# Примечание: момент из фразы в вызов; день не назван — дата модели (перенос, ответ на вопрос)
# или ближайший будущий; времени нет — время модели; задача с днём и без времени — весь день
def _withMoment(
    call: FunctionCall,
    field: str,
    day: date | None,
    said: SaidTime | None,
    turn: TurnInput,
    *,
    nearestModel: bool,
) -> FunctionCall:
    zone = ZoneInfo(turn.tz)
    now = turn.now.astimezone(zone)
    model = _moment(call.arguments.get(field))
    current = model or _focusedMoment(call, turn)
    if said is not None:
        base = day or _modelDay(call, field, turn)
        moment = _combine(base, said.hour, said.minute, zone) if base else None
        moment = moment or _nearest(said.hour, said.minute, now, zone)
    elif day is not None and current is not None:
        local = current.astimezone(zone)
        moment = _combine(day, local.hour, local.minute, zone)
    elif day is not None and call.name == "create_task" and field == STARTS_AT:
        return FunctionCall(call.name, {**call.arguments, STARTS_AT: day.isoformat()})
    elif model is not None and nearestModel and call.name != "update_item":
        # день не назван («ближе к 12 ночи»): сегодня, если это время ещё впереди
        local = model.astimezone(zone)
        moment = _nearest(local.hour, local.minute, now, zone)
    else:
        return call
    arguments: dict[str, object] = {**call.arguments, field: moment.isoformat()}
    end = _moment(arguments.get(ENDS_AT))
    if end is not None and model is not None:
        arguments[ENDS_AT] = (end + (moment - model)).isoformat()
    return FunctionCall(call.name, arguments)


# Примечание: нынешний момент переносимой записи из фокуса (модель не передала время)
def _focusedMoment(call: FunctionCall, turn: TurnInput) -> datetime | None:
    if call.name != "update_item":
        return None
    brief = _focused(call, turn)
    return (brief.remindAt or brief.startsAt) if brief is not None else None


# Примечание: дата, которую знает модель: из ответа на вопрос или у переносимой записи
def _modelDay(call: FunctionCall, field: str, turn: TurnInput) -> date | None:
    if call.name != "update_item" and not turn.pending:
        return None
    model = _moment(call.arguments.get(field))
    if model is not None:
        return model.astimezone(ZoneInfo(turn.tz)).date()
    brief = _focused(call, turn)
    current = (brief.remindAt or brief.startsAt) if brief is not None else None
    return current.astimezone(ZoneInfo(turn.tz)).date() if current is not None else None


def _combine(day: date, hour: int, minute: int, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=zone)


# Примечание: день не назван — сегодня, если это время ещё впереди, иначе завтра
def _nearest(hour: int, minute: int, now: datetime, zone: ZoneInfo) -> datetime:
    today = _combine(now.date(), hour, minute, zone)
    return today if today > now else _combine(now.date() + timedelta(days=1), hour, minute, zone)


# Примечание: момент ISO 8601 со смещением; дата без времени и прочее — None
def _moment(value: object) -> datetime | None:
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None
