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
from brain.spoken_time import (
    SaidTime,
    Spoken,
    monthLabel,
    nearestDate,
    normalize,
    parseSpoken,
    remindOffset,
)
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
ASK_TASK_DAY: Final = "На какой день поставить задачу?"
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
ASK_EVENT_DAY_TIME: Final = "На какой день и во сколько поставить встречу?"
# сколько прошлых реплик пользователя смотреть в одном уточнении (история агента — 10 сообщений)
MAX_CLARIFICATION_TURNS: Final = 5
_NEW_REQUEST_RE: Final = re.compile(
    r"^\s*(?:пожалуйста\s+|а\s+)?(?:созда|запланир|назнач|постав|добав|запиш|напомн|удал|отмен|"
    r"перенес|найди|покажи|что\s+у\s+меня|когда\s+у\s+меня)",
    re.IGNORECASE,
)
_EVENT_RE: Final = re.compile(r"\b(?:встреч\w*|созвон\w*|собрани\w*)")
_CREATE_RE: Final = re.compile(r"\b(?:созда|запланир|назнач|постав|добав|запиш)\w*")
_REMIND_WORD_RE: Final = re.compile(r"\bнапомн")
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
        if _isUndatedTask(call, field):
            return _withRequest(_askTaskDay(), turn)
        return _allDay(call, turn) if _isTask(call, field, turn) else call
    pending = turn.pending or {}
    context = _dialogContext(turn)
    day = spoken.day or _monthDayAnswer(pending, turn) or _askedSpoken(pending, turn).day
    request = _requestSpoken(pending, turn)
    day = day or _savedDay(pending) or context.day or request.day
    earlier = _savedTime(pending) or context.time or request.time
    said = spoken.time or _partAnswer(turn.text, earlier) or earlier
    isTask = _isTask(call, field, turn)
    # у задачи времени нет — весь день: «утра или вечера?» не спрашиваем
    ask = _question(call, field, spoken, turn, day=day, said=None if isTask else said)
    if ask is None and day is None and _isUndatedTask(call, field):
        ask = _askTaskDay()
    if ask is not None:
        return _withRequest(ask, turn)
    hasDayContext = bool(pending) or context.day is not None
    guarded = _withMoment(call, field, day, said, turn, nearestModel=not hasDayContext)
    return _allDay(guarded, turn) if isTask else guarded


# Примечание: задача (не напоминание, не встреча): срок — дата на весь день
def _isTask(call: FunctionCall, field: str, turn: TurnInput) -> bool:
    if field != STARTS_AT or call.name == "create_event":
        return False
    if call.name == "create_task":
        return True
    brief = _focused(call, turn)
    return (call.arguments.get("kind") or (brief.kind if brief else None)) == "task"


# Примечание: момент срока задачи → её дата в поясе пользователя (весь день), без конца
def _allDay(call: FunctionCall, turn: TurnInput) -> FunctionCall:
    moment = _moment(call.arguments.get(STARTS_AT))
    if moment is None:
        return call
    day = moment.astimezone(ZoneInfo(turn.tz)).date()
    arguments = {k: v for k, v in call.arguments.items() if k != ENDS_AT}
    return FunctionCall(call.name, {**arguments, STARTS_AT: day.isoformat()})


# Примечание: просьба создать встречу или напоминание без времени — вопрос до модели: она
# придумывала время (00:00) или отвечала бессвязным текстом; день, если назван, — в pending
def askMissingTime(turn: TurnInput, spoken: Spoken) -> Ask | None:
    lowered = turn.text.lower()
    if (turn.pending and not isNewRequest(turn.text)) or clarificationTexts(turn):
        return None
    if not spoken.supported or spoken.time is not None or spoken.month:
        return None
    # «напомни за 40 минут» — время есть: от начала записи
    if _PART_OF_DAY_RE.search(lowered) or remindOffset(turn.text) is not None:
        return None
    isEvent = _EVENT_RE.search(lowered) is not None and _CREATE_RE.search(lowered) is not None
    if isEvent and spoken.day is None:
        pending: JsonObject = {
            "kind": "ask",
            "question": ASK_EVENT_DAY_TIME,
            REQUEST_KEY: turn.text,
        }
        return Ask(ASK_EVENT_DAY_TIME, [], [STARTS_AT], pending)
    if not isEvent and not _REMIND_WORD_RE.search(lowered):
        return None
    question = ASK_EVENT_TIME if isEvent else ASK_REMIND_TIME
    pending = {"kind": "ask", "question": question, REQUEST_KEY: turn.text}
    if spoken.day is not None:
        pending[SAID_DAY_KEY] = spoken.day.isoformat()
    return Ask(question, list(TIME_OPTIONS), [STARTS_AT if isEvent else REMIND_AT], pending)


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
    found = Spoken()
    for earlier in reversed(clarificationTexts(turn)):
        spoken = parseSpoken(earlier, turn.now, turn.tz)
        if not spoken.supported or spoken.problem is not None:
            continue
        found = Spoken(day=found.day or spoken.day, time=found.time or spoken.time)
    return found


# Примечание: прошлые реплики пользователя в текущем уточнении (от ранней к поздней): пока бот
# отвечал вопросом — неважно, кто спросил (модель или код) и чем кончается текст; новая
# просьба уточнение обрывает
def clarificationTexts(turn: TurnInput) -> list[str]:
    if isNewRequest(turn.text):
        return []
    history = list(turn.history)
    texts: list[str] = []
    while len(history) >= 2 and history[-1].role == "assistant" and _isQuestion(history[-1].text):  # noqa: PLR2004
        user = history[-2]
        history = history[:-2]
        if user.role != "user" or len(texts) >= MAX_CLARIFICATION_TURNS:
            break
        texts.insert(0, user.text)
        if isNewRequest(user.text):
            break
    return texts


# Примечание: реплика начинает новую просьбу, а не отвечает на вопрос бота
def isNewRequest(text: str) -> bool:
    return _NEW_REQUEST_RE.match(text) is not None


def _isQuestion(text: str) -> bool:
    return "?" in text or "уточни" in text.lower()


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


# Примечание: исходная просьба из pending своего вопроса («…01.13…, напомни в обед» → «в обед»)
def _requestSpoken(pending: Mapping[str, object], turn: TurnInput) -> Spoken:
    request = pending.get(REQUEST_KEY)
    if not isinstance(request, str):
        return Spoken()
    spoken = parseSpoken(request, turn.now, turn.tz)
    return Spoken(day=spoken.day, time=spoken.time) if spoken.supported else Spoken()


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
    isOurQuestion = (
        pending.get("question") in {ASK_EVENT_TIME, ASK_REMIND_TIME, ASK_DAY}
        or REQUEST_KEY in pending
    )
    # время модели (или дата без времени у встречи) — придумано: пользователь его не называл
    return not pending or isOurQuestion


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


# Примечание: новая задача без срока: у задачи всегда есть день — модель его не дала, пользователь
# не назвал
def _isUndatedTask(call: FunctionCall, field: str) -> bool:
    if call.name != "create_task" or field != STARTS_AT:
        return False
    return call.arguments.get("kind", "task") == "task" and not call.arguments.get(STARTS_AT)


def _askTaskDay() -> Ask:
    pending: JsonObject = {"kind": "ask", "question": ASK_TASK_DAY}
    return Ask(ASK_TASK_DAY, list(DAY_OPTIONS), [STARTS_AT], pending)


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
