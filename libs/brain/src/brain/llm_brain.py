"""LlmBrain: ход пользователя через модель с инструментами (реализация Brain из J2).

Цикл: модель → вызов инструмента через ToolExecutor → summary назад модели → снова модель,
не больше maxSteps вызовов модели. Итог определяет код, а не текст модели: done — был успешный
изменяющий вызов, answer — только чтение или текст, clarify — ask_user или конфликт встреч,
error — код LLM или инструмента. Защиты держит код: повтор того же вызова не выполняется,
id записи — только известный, время без смещения и validation_error / past_time — одна попытка
исправиться, «записал» без изменяющего вызова — llm_no_action.
resume — ответ кнопкой на уточнение: вызов из pending достраивается выбранным вариантом без модели.
"""

import asyncio
import json
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Final, Literal, cast, get_args
from zoneinfo import ZoneInfo

from brain.llm import FunctionCall, JsonObject, LlmClient, LlmError, LlmMessage, LlmReply
from brain.prompt import ASK_USER, NOTHING, PROMPT_VERSION, buildSystemPrompt
from brain.spoken_time import (
    Spoken,
    nearestDate,
    parseSpoken,
    remindOffset,
    withoutOffset,
    withoutTime,
)
from brain.time_guard import (
    ASK_REMIND_TIME,
    REQUEST_KEY,
    TIME_OPTIONS,
    Ask,
    askMissingTime,
    clarificationTexts,
    dayChoice,
    guardTime,
    isNewRequest,
    partChoice,
    savedDay,
    timeNotApplied,
)
from common.logging import getLogger
from contracts.agent_side import (
    BrainErrorCode,
    Clarify,
    ExecutedAction,
    ItemBrief,
    ToolErrorCode,
    ToolExecutor,
    ToolResult,
    ToolSpec,
    TurnInput,
    TurnResult,
    Usage,
)

DEFAULT_MAX_STEPS: Final = 4
# Ход агента — 40 с вместе с ожиданием лока (до 10 с) и вызовами MCP: модели — не больше 25 с
DEFAULT_TURN_BUDGET_S: Final = 25.0
# Меньше секунды до дедлайна — новый вызов модели не начинаем: не успеет ответить
MIN_CALL_S: Final = 1.0
# Изменяющие инструменты J3: только их успешный вызов даёт outcome=done
MUTATING_TOOLS: Final = frozenset(
    {"create_task", "create_event", "update_item", "complete_item", "delete_item"}
)
# GigaChat путает update_item, complete_item и delete_item: на «отмени» отмечает выполненной,
# на «я сходил» удаляет; промпт и описания не помогают (проверено на GigaChat-2-Max). Поэтому при
# явной просьбе удалить или отметить сделанное лишние из этих трёх инструментов модели не даём
_DELETE_RE: Final = re.compile(
    r"\b(удали|удалить|удалите|удаляй|отмени|отменить|отмените|отменяй|убери|убрать|уберите|"
    r"сотри|стереть)\b",
    re.IGNORECASE,
)
_DONE_RE: Final = re.compile(
    r"\b(сделал\w*|сделан\w*|выполнил\w*|выполнен\w*|сходил\w*|закончил\w*|отметь)"
    # «я помыл машину», «уже купила хлеб» — глагол прошедшего времени о себе; модель на «я помыл»
    # меняла запись (update_item) вместо отметки. Не про сделанное: «я хотел», «я забыл» и т. п.
    r"|^\s*(?:я|мы)\s+(?:уже\s+)?(?!(?:хотел|забыл|думал|мог|не|был)\w*\b)\w+(?:л|ла|ли)\b"
    r"|\bуже\s+(?!(?:хотел|забыл|думал|мог|был)\w*\b)\w+(?:л|ла|ли)\b",
    re.IGNORECASE,
)
HIDDEN_ON_DELETE: Final = frozenset({"update_item", "complete_item"})
# «отмени все планы на неделю», «удали всё на завтра»: модель только находит записи за период
# (list_items), удаляет код — после «Да» на вопрос «Удалить N записей?»
# «все», «всё» или множественное число («удали планы на сегодня», «отмени встречи на неделю»)
_BULK_RE: Final = re.compile(
    r"\b(вс[её]|все|планы|дела|записи|события|встречи|напоминания|задачи)\b", re.IGNORECASE
)
_WEEK_RE: Final = re.compile(r"недел", re.IGNORECASE)
_NEXT_WEEK_RE: Final = re.compile(r"следующ\w*\s+недел", re.IGNORECASE)
# «удали все планы» без периода — все записи: на десять лет в обе стороны
ALL_PLANS_SPAN: Final = timedelta(days=3650)
_TASK_RE: Final = re.compile(r"\bзадач", re.IGNORECASE)
# «да», «удаляй» — ответ, а не новая просьба, даже если начинается с «удали»
SHORT_ANSWER_WORDS: Final = 2
BULK_DELETE_TOOLS: Final = frozenset({"list_items"})
DELETE_MANY: Final = "delete_many"
ASK_DELETE_MANY: Final = "Удалить {count} {noun}: {titles}?"
DELETE_MANY_OPTION: Final = "Да, удалить"
DELETE_MANY_CANCELLED: Final = "Хорошо, ничего не удаляю."
TITLES_IN_QUESTION: Final = 5
# вид записей из фразы удаления; модель фильтр по виду теряла («все напоминания» удалили и
# встречу), поэтому фильтрует код; «все записи», «планы», «всё» — все виды
BULK_KINDS: Final = (
    (re.compile(r"напомина", re.IGNORECASE), "reminder"),
    (re.compile(r"встреч|созвон", re.IGNORECASE), "event"),
    (re.compile(r"задач", re.IGNORECASE), "task"),
)
_YES_RE: Final = re.compile(
    r"^(да|ага|угу|удаляй|удали|удалить|давай|конечно|ок|окей|все|всё)\b", re.IGNORECASE
)
HIDDEN_ON_DONE: Final = frozenset({"update_item", "delete_item"})
# После списка модели нечего делать: агент рисует его из data, второй вызов модели — лишние секунды
FINAL_TOOLS: Final = frozenset({"list_items"})
TIME_FIELDS: Final = ("starts_at", "ends_at", "remind_at")
ID_FIELDS: Final = ("id", "exclude_id")
MAX_OPTIONS: Final = 4
SHORT_ID_LENGTH: Final = 8
ASK_SLOT: Final = "В это время уже есть: {busy}. Выбери свободное время:"
NO_SLOTS: Final = "В это время уже есть: {busy}, свободных окон рядом нет. Назови другое время."
HINT_OFFSET: Final = (
    "Время {value} без смещения. Передай ISO 8601 со смещением пояса пользователя, "
    "например 2026-10-25T10:00:00+03:00, или дату YYYY-MM-DD для записи на весь день."
)
HINT_UNKNOWN_ID: Final = (
    "Записи с id {value} нет ни в фокусе, ни в результатах. Сначала найди её через search_items."
)
HINT_UNKNOWN_TOOL: Final = "Функции {value} нет. Используй только функции из списка."
HINT_DUPLICATE: Final = "Этот вызов уже выполнен, его результат выше. Не повторяй его."
# «Напомни …» без времени: модель создавала задачу без срока — напоминанию нужно время
_REMIND_RE: Final = re.compile(r"\bнапомн", re.IGNORECASE)
MOMENT_FIELDS: Final = ("starts_at", "remind_at")
# «напомни об этой встрече за 40 минут» — встреча из фокуса, а не найденная по названию
_THIS_RE: Final = re.compile(
    r"\b(?:эт(?:ой|у|а|от|им|ом)|не[её]|ней|е[её]|она|него|н[её]м)\b", re.IGNORECASE
)
# «напомни за 40 минут до встречи» без имени — тоже встреча из фокуса: модель искала по слову
# «встреча» и брала первую попавшуюся
_GENERIC_REMIND_WORDS: Final = frozenset(
    {
        "напомни",
        "напомнить",
        "напомните",
        "мне",
        "о",
        "об",
        "про",
        "до",
        "перед",
        "начала",
        "встреча",
        "встречи",
        "встрече",
        "встречу",
        "пожалуйста",
    }
)
# перенос принят, а время записи другое — «Изменено» показывать нельзя
TIME_NOT_APPLIED: Final = "Не получилось поменять время. Скажи ещё раз, на какое время перенести."
# «когда у меня маникюр» — это поиск записи, а не список за две недели
_WHEN_RE: Final = re.compile(r"^\s*(?:а\s+)?когда\b", re.IGNORECASE)
_PERIOD_RE: Final = re.compile(r"сегодня|завтра|недел|месяц|выходн", re.IGNORECASE)
BAD_CHOICE: Final = "Не понял выбор — повтори просьбу."

ASK_USER_SPEC: Final = ToolSpec(
    name=ASK_USER,
    description=(
        "Задать пользователю уточняющий вопрос, когда для вызова функции не хватает данных. "
        "Если ответ — значение одного поля вызова, укажи tool, args, field и values: "
        "тогда выбор кнопки сразу выполнит вызов."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Короткий вопрос пользователю"},
            "missing": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Каких полей не хватает, например starts_at",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "2–4 коротких варианта ответа для кнопок",
            },
            "tool": {"type": "string", "description": "Функция, которую вызвать после ответа"},
            # GigaChat отвергает object без properties (422), когда функций в запросе мало
            "args": {
                "type": "object",
                "properties": {},
                "description": "Уже известные аргументы этой функции",
            },
            "field": {"type": "string", "description": "Поле, которое заполняет ответ"},
            "values": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Значение поля для каждого варианта, в том же порядке",
            },
        },
        "required": ["question", "missing", "options"],
    },
)

type CorrectionReason = Literal["offset", "unknown_id", "unknown_tool", "tool_error"]

_NAIVE_TIME_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$")
# Модель «отчиталась» об изменении, которого не было: такой текст пользователю не показываем
_SUCCESS_CLAIM_RE: Final = re.compile(
    r"\b(создал|записал|добавил|сохранил|перен[её]с|удалил|отметил|запланировал|"
    r"напомню|готово|создан[аоы]?|записан[аоы]?|добавлен[аоы]?|сохранен[аоы]?|"
    r"перенесен[аоы]?|удален[аоы]?)\b",
    re.IGNORECASE,
)
_TOOL_ERRORS: Final = frozenset(get_args(ToolErrorCode.__value__))
_RETRYABLE_TOOL_ERRORS: Final = frozenset({"validation_error", "past_time"})

_log = getLogger(__name__)


# Примечание: состояние одного хода: диалог с моделью, выполненные вызовы, расход, поправки
@dataclass
class _Turn:
    messages: list[LlmMessage]
    knownIds: set[str]
    actions: list[ExecutedAction] = field(default_factory=list)
    corrections: set[CorrectionReason] = field(default_factory=set)
    llmCalls: int = 0
    tokensIn: int = 0
    tokensOut: int = 0
    model: str = ""
    deadline: float = 0.0


# Примечание: Brain на LLM; клиент модели (GigaChat или фейк) выбирает агент
class LlmBrain:
    def __init__(
        self,
        llm: LlmClient,
        model: str,
        maxSteps: int = DEFAULT_MAX_STEPS,
        turnBudgetS: float = DEFAULT_TURN_BUDGET_S,
    ) -> None:
        self._llm = llm
        self._model = model
        self._maxSteps = maxSteps
        self._turnBudgetS = turnBudgetS

    # Примечание: новый ход: цикл модели с инструментами; исключения LLM наружу не выходят
    async def run(self, turn: TurnInput, tools: ToolExecutor) -> TurnResult:
        started = time.perf_counter()
        if turn.pending and isNewRequest(turn.text) and len(turn.text.split()) > SHORT_ANSWER_WORDS:
            # новая просьба вместо ответа: старый вопрос закрыт, иначе ответ к нему примешивается
            turn = turn.model_copy(update={"pending": None})
        pending = turn.pending or {}
        if pending.get("kind") == DELETE_MANY:
            # ответ текстом на «Удалить N записей?»: «да» — удалить, иначе — ничего не трогать
            state = _Turn(messages=[], knownIds=set(), model=self._model)
            confirmed = _YES_RE.match(turn.text.strip()) is not None
            outcome = await _deleteMany(pending, confirmed, tools, state, started)
            _logTurn(outcome, state)
            return outcome
        if (choice := partChoice(turn)) is not None:
            # «утро» словом на «Утра или вечера?» — как нажатие кнопки
            return await self.resume(turn, str(choice), tools)
        if (day := dayChoice(turn)) is not None:
            # «сегодня» словом на «На какой день?» — как нажатие кнопки, без модели
            return await self.resume(turn, str(day), tools)
        early = _Turn(messages=[], knownIds=set(), model=self._model)
        if (earlyOutcome := await _beforeModel(turn, tools, early, started)) is not None:
            _logTurn(earlyOutcome, early)
            return earlyOutcome
        specs = _visibleTools(turn.text, await tools.listTools())
        state = _Turn(
            messages=buildMessages(_forModelTurn(turn)),
            knownIds={str(b.id) for b in turn.focus},
            model=self._model,
            deadline=time.monotonic() + self._turnBudgetS,
        )
        try:
            # «удали всё»: уточнять нечего — модель только находит записи за период
            ask = [] if _isBulkDelete(turn.text) else [ASK_USER_SPEC]
            result = await self._loop(turn, tools, [*specs, *ask], state, started)
        except LlmError as exc:
            # изменение уже сохранено — это успех хода: подтверждение агент строит из data
            hasMutation = _hasMutation(state.actions)
            result = (
                _result("done", state, started) if hasMutation else _error(exc.code, state, started)
            )
        _logTurn(result, state)
        return result

    # Примечание: кнопка уточнения: «call» — вызов без модели по выбранному варианту,
    # «ask» — подпись кнопки (она уже в turn.text) уходит модели обычным ходом
    async def resume(self, turn: TurnInput, choice: str, tools: ToolExecutor) -> TurnResult:
        started = time.perf_counter()
        pending = turn.pending or {}
        if pending.get("kind") == "ask":
            return await self.run(turn, tools)
        state = _Turn(messages=[], knownIds={str(b.id) for b in turn.focus}, model=self._model)
        index = int(choice) if choice.isdigit() else -1
        if pending.get("kind") == DELETE_MANY:
            confirmed = index == 0
            outcome = await _deleteMany(pending, confirmed, tools, state, started)
            _logTurn(outcome, state)
            return outcome
        tool, args = pending.get("tool"), pending.get("args")
        choices = [c for c in _list(pending.get("choices")) if isinstance(c, dict)]
        if (
            pending.get("kind") != "call"
            or not isinstance(tool, str)
            or not 0 <= index < len(choices)
        ):
            return _result("answer", state, started, say=BAD_CHOICE)
        base = {str(k): v for k, v in args.items()} if isinstance(args, dict) else {}
        call = FunctionCall(tool, {**base, **{str(k): v for k, v in choices[index].items()}})
        result = await tools.call(call.name, call.arguments)
        state.actions.append(ExecutedAction(name=call.name, args=call.arguments, result=result))
        # модели нет — исправлять аргументы некому: ошибка инструмента сразу заканчивает ход
        state.corrections.add("tool_error")
        outcome = _afterCall(call, result, turn, state, started) or _final("", state, started)
        _logTurn(outcome, state)
        return outcome

    async def _loop(
        self,
        turn: TurnInput,
        tools: ToolExecutor,
        specs: Sequence[ToolSpec],
        state: _Turn,
        started: float,
    ) -> TurnResult:
        names = {spec.name for spec in specs}
        for _ in range(self._maxSteps):
            reply = await self._complete(state, specs)
            call = reply.functionCall
            if call is None:
                return _final(reply.text, state, started)
            if call.name == ASK_USER:
                return _askUser(call.arguments, names, turn, state, started)
            if (hint := _rejection(call, names, state)) is not None:
                reason, text = hint
                if reason in state.corrections:
                    return _error("llm_bad_output", state, started)
                state.corrections.add(reason)
                _answerModel(state, reply, {"error": text})
                continue
            if _isDuplicate(call, state.actions) or _isDuplicate(
                _guarded(call, turn), state.actions
            ):
                _answerModel(state, reply, {"error": HINT_DUPLICATE})
                continue
            stop, result = await _act(call, turn, tools, state, started)
            if stop is not None:
                return stop
            _answerModel(state, reply, _forModel(result))
        return _final("", state, started)

    # Примечание: вызов модели в пределах бюджета хода: агент оборвёт ход по своему таймауту,
    # и тогда пользователь не узнает даже о том, что уже сделано
    async def _complete(self, state: _Turn, specs: Sequence[ToolSpec]) -> LlmReply:
        remaining = state.deadline - time.monotonic()
        if remaining < MIN_CALL_S:
            raise LlmError("llm_timeout", "бюджет хода исчерпан")
        try:
            async with asyncio.timeout(remaining):
                reply = await self._llm.complete(state.messages, specs)
        except TimeoutError as exc:
            raise LlmError("llm_timeout", "модель не уложилась в бюджет хода") from exc
        state.llmCalls += 1
        state.tokensIn += reply.tokensIn
        state.tokensOut += reply.tokensOut
        state.model = reply.model or state.model
        return reply


# Примечание: инструменты хода; «удали» и «сделал» в одной фразе — неясно, оставляем все
def _visibleTools(text: str, specs: Sequence[ToolSpec]) -> list[ToolSpec]:
    isDelete = _DELETE_RE.search(text) is not None
    isDone = _DONE_RE.search(text) is not None
    if _isBulkDelete(text):
        return [spec for spec in specs if spec.name in BULK_DELETE_TOOLS]
    if _WHEN_RE.search(text) and not _PERIOD_RE.search(text):
        specs = [spec for spec in specs if spec.name != "list_items"]
    if isDelete == isDone:
        return list(specs)
    hidden = HIDDEN_ON_DELETE if isDelete else HIDDEN_ON_DONE
    return [spec for spec in specs if spec.name not in hidden]


def _isBulkDelete(text: str) -> bool:
    return _DELETE_RE.search(text) is not None and _BULK_RE.search(text) is not None


# Примечание: список за период по просьбе «удали всё» — вопрос «Удалить N записей?» с кнопкой;
# id записей — в pending, удаляет resume или ответ «да»; пустой список — обычный ответ «ничего нет»
def _askDeleteMany(
    call: FunctionCall, result: ToolResult, turn: TurnInput, state: _Turn, started: float
) -> TurnResult | None:
    if call.name != "list_items" or not result.ok or not _isBulkDelete(turn.text):
        return None
    kinds = {kind for pattern, kind in BULK_KINDS if pattern.search(turn.text)}
    items = [
        i
        for i in _list((result.data or {}).get("items"))
        if isinstance(i, dict) and (not kinds or i.get("kind") in kinds)
    ]
    ids = [str(i["id"]) for i in items if i.get("id")]
    if not ids:
        return None
    titles = [str(i.get("title") or "") for i in items[:TITLES_IN_QUESTION]]
    shown = ", ".join(f"«{t}»" for t in titles)
    if len(items) > TITLES_IN_QUESTION:
        shown += f" и ещё {len(items) - TITLES_IN_QUESTION}"
    say = ASK_DELETE_MANY.format(count=len(ids), noun=_recordsNoun(len(ids)), titles=shown)
    clarify = Clarify(
        missing=["confirm"],
        options=[DELETE_MANY_OPTION],
        pending={"kind": DELETE_MANY, "ids": list(ids)},
    )
    return _result("clarify", state, started, say=say, clarify=clarify)


# Примечание: «запись / записи / записей» по числу
def _recordsNoun(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:  # noqa: PLR2004 — правило русского склонения
        return "запись"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:  # noqa: PLR2004
        return "записи"
    return "записей"


# Примечание: подтверждено — delete_item по каждому id из pending; нет — ничего не удаляем
async def _deleteMany(
    pending: Mapping[str, object],
    confirmed: bool,
    tools: ToolExecutor,
    state: _Turn,
    started: float,
) -> TurnResult:
    ids = _strings(pending.get("ids"))
    if not confirmed or not ids:
        return _result("answer", state, started, say=DELETE_MANY_CANCELLED)
    for itemId in ids:
        args: JsonObject = {"id": itemId}
        result = await tools.call("delete_item", args)
        state.actions.append(ExecutedAction(name="delete_item", args=args, result=result))
    if _hasMutation(state.actions):
        return _result("done", state, started)
    return _error(_lastToolCode(state.actions), state, started)


# Примечание: итог хода в лог — без текста пользователя и ответа модели
def _logTurn(result: TurnResult, state: _Turn) -> None:
    _log.info(
        "brain_turn",
        prompt_version=PROMPT_VERSION,
        outcome=result.outcome,
        error_code=result.errorCode,
        actions=[a.name for a in result.actions],
        llm_calls=state.llmCalls,
        tokens_in=state.tokensIn,
        tokens_out=state.tokensOut,
    )


# Примечание: системный промпт с блоком времени и фокусом, история (≤10) и фраза пользователя
def buildMessages(turn: TurnInput) -> list[LlmMessage]:
    focus = "\n" + "\n".join(_focusLine(b, turn.tz) for b in turn.focus) if turn.focus else NOTHING
    system = buildSystemPrompt(turn.now, turn.tz, focus, _pendingText(turn.pending))
    if turn.asrLowConfidence:
        system += "\n\nФраза распознана из голоса неуверенно: если смысл неясен, переспроси."
    history = [LlmMessage(role=t.role, content=t.text) for t in turn.history]
    return [
        LlmMessage(role="system", content=system),
        *history,
        LlmMessage(role="user", content=_withToday(turn.text, turn.now, turn.tz)),
    ]


# Примечание: дата рядом с фразой: блок времени далеко, и после ответов с датами в истории
# («Завтра, пн 26 октября») GigaChat считал «завтра» от них — на день позже
def _withToday(text: str, now: datetime, tz: str) -> str:
    local = now.astimezone(ZoneInfo(tz))
    tomorrow = (local + timedelta(days=1)).date()
    return f"(сейчас {local:%Y-%m-%d %H:%M}, завтра {tomorrow.isoformat()})\n{text}"


# Примечание: «- 1f2e3d4c встреча «Созвон с Иваном» 2026-10-25T15:00+03:00»
def _focusLine(brief: ItemBrief, tz: str) -> str:
    kinds = {"task": "задача", "reminder": "напоминание", "event": "встреча"}
    moment = brief.moment
    when = (
        moment.astimezone(ZoneInfo(tz)).isoformat(timespec="minutes") if moment else "без времени"
    )
    return f"- {str(brief.id)[:8]} {kinds[brief.kind]} «{brief.title}» {when}"


# Примечание: вызов, который нельзя выполнять: неизвестная функция, чужой id, время без смещения
def _rejection(
    call: FunctionCall, names: set[str], state: _Turn
) -> tuple[CorrectionReason, str] | None:
    if call.name not in names:
        return "unknown_tool", HINT_UNKNOWN_TOOL.format(value=call.name)
    for name in ID_FIELDS:
        itemId = call.arguments.get(name)
        if itemId is not None and not _isKnownId(itemId, state.knownIds):
            return "unknown_id", HINT_UNKNOWN_ID.format(value=itemId)
    for name in TIME_FIELDS:
        value = call.arguments.get(name)
        if isinstance(value, str) and _NAIVE_TIME_RE.match(value):
            return "offset", HINT_OFFSET.format(value=value)
    return None


# Примечание: «напомни …» без времени, а модель создаёт запись без момента — сначала спросить время
def _isReminderWithoutTime(call: FunctionCall, turn: TurnInput) -> bool:
    if call.name != "create_task" or not _REMIND_RE.search(turn.text):
        return False
    if remindOffset(turn.text) is not None:
        return False
    return not any(_moment(call.arguments.get(name)) for name in MOMENT_FIELDS)


# Примечание: вызов инструмента под защитами времени: напоминание без времени и неясный момент —
# вопрос без вызова; принятый перенос с другим временем — не «Изменено»; итог хода или None
async def _act(
    call: FunctionCall, turn: TurnInput, tools: ToolExecutor, state: _Turn, started: float
) -> tuple[TurnResult | None, ToolResult]:
    skipped = ToolResult(ok=True, summary="")
    before = _remindBefore(call, turn, _eventStart(call, turn, state))
    if before is not None:
        result = await tools.call(before.name, before.arguments)
        state.actions.append(ExecutedAction(name=before.name, args=before.arguments, result=result))
        return _afterCall(before, result, turn, state, started), result
    if _isReminderWithoutTime(call, turn):
        pending: JsonObject = {"kind": "ask", "question": ASK_REMIND_TIME, **savedDay(turn)}
        ask = Ask(ASK_REMIND_TIME, list(TIME_OPTIONS), ["remind_at"], pending)
        return _askGuard(ask, turn, state, started), skipped
    guarded = guardTime(call, turn)
    if isinstance(guarded, Ask):
        return _askGuard(guarded, turn, state, started), skipped
    result = await tools.call(guarded.name, guarded.arguments)
    state.actions.append(ExecutedAction(name=guarded.name, args=guarded.arguments, result=result))
    if timeNotApplied(guarded, result):
        return _result("answer", state, started, say=TIME_NOT_APPLIED), result
    return _afterCall(guarded, result, turn, state, started), result


# Примечание: вызов в том виде, в каком он уйдёт в MCP: в state.actions лежат такие — повтор
# модели сравниваем и с ними
def _guarded(call: FunctionCall, turn: TurnInput) -> FunctionCall:
    guarded = guardTime(call, turn)
    return call if isinstance(guarded, Ask) else guarded


# Примечание: что код решает без модели (модель здесь ошибалась): дата с проблемой — вопрос;
# «удали все планы на завтра» — список за период и «Удалить N?»; встреча или напоминание без
# времени — «Во сколько?» (модель придумывала 00:00 или отвечала бессвязным текстом)
async def _beforeModel(
    turn: TurnInput, tools: ToolExecutor, state: _Turn, started: float
) -> TurnResult | None:
    spoken = parseSpoken(turn.text, turn.now, turn.tz)
    if spoken.problem is not None:
        return _askProblem(spoken.problem.question, spoken.problem.options, turn, state, started)
    bulk = _bulkTurn(turn)
    if bulk is not None and (period := _bulkPeriod(bulk)) is not None:
        args: JsonObject = {"from": period[0].isoformat(), "to": period[1].isoformat()}
        call = FunctionCall("list_items", args)
        result = await tools.call(call.name, args)
        state.actions.append(ExecutedAction(name=call.name, args=args, result=result))
        return _askDeleteMany(call, result, bulk, state, started) or _result(
            "answer", state, started
        )
    if (before := _remindBeforeFocused(turn, spoken)) is not None:
        result = await tools.call(before.name, before.arguments)
        state.actions.append(ExecutedAction(name=before.name, args=before.arguments, result=result))
        if not result.ok:
            return _error(
                _toolCode(result.error.code if result.error else "internal"), state, started
            )
        return _result("done", state, started)
    if (ask := askMissingTime(turn, spoken)) is not None:
        return _askGuard(ask, turn, state, started)
    return None


# Примечание: «напомни об этой встрече за 40 минут» — встреча в фокусе: remind_at считает код, без
# модели (защита «напомни без времени» спрашивала «Во сколько напомнить?»)
def _remindBeforeFocused(turn: TurnInput, spoken: Spoken) -> FunctionCall | None:
    isFocused = _THIS_RE.search(turn.text) or set(withoutOffset(turn.text).split()) <= (
        _GENERIC_REMIND_WORDS
    )
    if not _REMIND_RE.search(turn.text) or not isFocused:
        return None
    # день или время названы («встреча завтра в 15, напомни об этом за час») — это новая запись
    if spoken.day is not None or spoken.time is not None:
        return None
    event = next(
        (b for b in reversed(turn.focus) if b.kind == "event" and b.startsAt and not b.allDay), None
    )
    if event is None:
        return None
    return _remindBefore(FunctionCall("update_item", {"id": str(event.id)}), turn, event.startsAt)


# Примечание: перенос напоминания встречи «за N минут»: remind_at = начало − смещение, считает код;
# время встречи не трогаем (guardTime у встречи переложил бы remind_at в starts_at)
def _remindBefore(
    call: FunctionCall, turn: TurnInput, startsAt: datetime | None
) -> FunctionCall | None:
    offset = remindOffset(turn.text)
    if call.name != "update_item" or offset is None or startsAt is None:
        return None
    if not _REMIND_RE.search(turn.text):
        return None
    remindAt = (startsAt - offset).astimezone(ZoneInfo(turn.tz))
    return FunctionCall(
        call.name, {"id": call.arguments.get("id"), "remind_at": remindAt.isoformat()}
    )


# Примечание: начало встречи, которую меняет вызов: из фокуса или из найденного в этом ходе
def _eventStart(call: FunctionCall, turn: TurnInput, state: _Turn) -> datetime | None:
    itemId = str(call.arguments.get("id") or "").lower()
    if len(itemId) < SHORT_ID_LENGTH:
        return None
    for brief in turn.focus:
        if str(brief.id).startswith(itemId) and brief.kind == "event" and not brief.allDay:
            return brief.startsAt
    for action in state.actions:
        for item in _foundItems(action.result.data):
            if str(item.get("id", "")).startswith(itemId) and item.get("kind") == "event":
                return _moment(item.get("starts_at"))
    return None


# Примечание: записи из data инструмента: item, items, results[].item (формы J3)
def _foundItems(data: Mapping[str, object] | None) -> list[Mapping[str, object]]:
    if not data:
        return []
    raw = [data.get("item"), *_list(data.get("items"))]
    raw.extend(r.get("item") for r in _list(data.get("results")) if isinstance(r, dict))
    return [item for item in raw if isinstance(item, dict)]


# Примечание: задача («добавь задачу … завтра в 5») — модели без времени: срок задачи — дата на весь
# день, а на «в 5» модель спрашивала «утра или вечера?»
def _forModelTurn(turn: TurnInput) -> TurnInput:
    if not _TASK_RE.search(turn.text) or parseSpoken(turn.text, turn.now, turn.tz).time is None:
        return turn
    return turn.model_copy(update={"text": withoutTime(turn.text)})


# Примечание: просьба удалить всё — эта фраза или прошлая в уточнении («Удали все планы» →
# «На какой срок?» → «все» / «на завтра»): текст для разбора — просьба вместе с ответом
def _bulkTurn(turn: TurnInput) -> TurnInput | None:
    if _isBulkDelete(turn.text):
        return turn
    earlier = next((t for t in reversed(clarificationTexts(turn)) if _isBulkDelete(t)), None)
    if earlier is None:
        return None
    return turn.model_copy(update={"text": f"{earlier} {turn.text}"})


# Примечание: период удаления: день, эта / следующая неделя, месяц; не назван — все записи
# (с запасом в обе стороны); фраза сложнее правил кода — None, период найдёт модель
def _bulkPeriod(turn: TurnInput) -> tuple[date, date] | None:
    spoken = parseSpoken(turn.text, turn.now, turn.tz)
    if not spoken.supported:
        return None
    if spoken.day is not None:
        return spoken.day, spoken.day + timedelta(days=1)
    today = turn.now.astimezone(ZoneInfo(turn.tz)).date()
    monday = today - timedelta(days=today.weekday())
    if _NEXT_WEEK_RE.search(turn.text):
        return monday + timedelta(days=7), monday + timedelta(days=14)
    if _WEEK_RE.search(turn.text):
        return monday, monday + timedelta(days=7)
    if spoken.month is not None:
        first = nearestDate(1, spoken.month, today.replace(day=1)) or today
        last = nearestDate(1, spoken.month % 12 + 1, first + timedelta(days=1)) or first
        return first, last
    return today - ALL_PLANS_SPAN, today + ALL_PLANS_SPAN


# Примечание: вопрос о дате до модели; просьба — в pending, ответ датой её дополнит
def _askProblem(
    question: str, options: list[str], turn: TurnInput, state: _Turn, started: float
) -> TurnResult:
    request = (turn.pending or {}).get(REQUEST_KEY) or turn.text
    pending: JsonObject = {"kind": "ask", "question": question, REQUEST_KEY: request}
    return _askGuard(Ask(question, options, ["starts_at"], pending), turn, state, started)


# Примечание: вопрос из time_guard вместо вызова: варианты-кнопки и pending для ответа
def _askGuard(ask: Ask, turn: TurnInput, state: _Turn, started: float) -> TurnResult:
    clarify = Clarify(missing=ask.missing, options=ask.options, pending=ask.pending)
    focus = [b.id for b in turn.focus]
    return _result("clarify", state, started, say=ask.question, clarify=clarify, focus=focus)


# Примечание: момент ISO 8601 со смещением из аргумента модели; дата без времени и прочее — None
def _moment(value: object) -> datetime | None:
    if not isinstance(value, str) or "T" not in value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


# Примечание: короткий id (первые 8 символов) подходит, если он однозначно из известных
def _isKnownId(value: object, knownIds: set[str]) -> bool:
    text = str(value).lower()
    return len(text) >= SHORT_ID_LENGTH and any(known.startswith(text) for known in knownIds)


def _isDuplicate(call: FunctionCall, actions: Sequence[ExecutedAction]) -> bool:
    return any(a.name == call.name and a.args == call.arguments for a in actions)


# Примечание: что делать после вызова: ошибка, конфликт или список заканчивают ход, иначе — дальше
def _afterCall(
    call: FunctionCall, result: ToolResult, turn: TurnInput, state: _Turn, started: float
) -> TurnResult | None:
    if not result.ok:
        code = result.error.code if result.error is not None else "internal"
        if code in _RETRYABLE_TOOL_ERRORS and "tool_error" not in state.corrections:
            state.corrections.add("tool_error")
            return None
        return _error(_toolCode(code), state, started)
    if (ask := _askDeleteMany(call, result, turn, state, started)) is not None:
        return ask
    state.knownIds.update(str(i) for i in _itemIds(result.data))
    data = result.data or {}
    if data.get("status") == "conflict":
        return _conflict(call, data, turn, state, started)
    if call.name in FINAL_TOOLS:
        return _result("answer", state, started)
    return None


# Примечание: модель закончила текстом: итог по вызовам, а не по словам модели
def _final(text: str, state: _Turn, started: float) -> TurnResult:
    if _hasMutation(state.actions):
        return _result("done", state, started)
    # после чтения агент рисует данные инструмента, текст модели («Готово, вот что нашёл») не виден
    if any(a.result.ok for a in state.actions):
        return _result("answer", state, started, say=text.strip() or None)
    # все вызовы с ошибкой, а модель молчит: без кода агент показал бы «Готово.» за пустой ход
    if state.actions and not text.strip():
        return _error(_lastToolCode(state.actions), state, started)
    if _SUCCESS_CLAIM_RE.search(text):
        return _error("llm_no_action", state, started)
    if not text.strip():
        return _error("llm_bad_output", state, started)
    return _result("answer", state, started, say=text.strip())


# Примечание: ask_user → уточнение; pending «call» достраивается по кнопке без модели (resume),
# «ask» — ответ пойдёт модели обычным ходом
def _askUser(
    raw: JsonObject, names: set[str], turn: TurnInput, state: _Turn, started: float
) -> TurnResult:
    question = str(raw.get("question") or "").strip()
    options = _strings(raw.get("options"))[:MAX_OPTIONS]
    missing = _strings(raw.get("missing")) or ["details"]
    pending = _pendingCall(raw, names, len(options), state) or {"kind": "ask", "question": question}
    clarify = Clarify(missing=missing, options=options, pending=pending)
    focus = [b.id for b in turn.focus]
    return _result("clarify", state, started, say=question or None, clarify=clarify, focus=focus)


# Примечание: вызов из ask_user, который кнопка выполнит без модели; каждый вариант проходит те же
# защиты, что и вызов модели (функция, id, смещение) — иначе вопрос остаётся свободным
def _pendingCall(
    raw: JsonObject, names: set[str], optionCount: int, state: _Turn
) -> JsonObject | None:
    tool, args, fieldName = raw.get("tool"), raw.get("args"), raw.get("field")
    values = _strings(raw.get("values"))[:optionCount]
    if not isinstance(tool, str) or not isinstance(fieldName, str) or tool == ASK_USER:
        return None
    if optionCount == 0 or len(values) != optionCount:
        return None
    base = {str(k): v for k, v in args.items()} if isinstance(args, dict) else {}
    choices: list[object] = [{fieldName: value} for value in values]
    for value in values:
        if _rejection(FunctionCall(tool, {**base, fieldName: value}), names, state) is not None:
            return None
    return {"kind": "call", "tool": tool, "args": base, "choices": choices}


# Примечание: встреча пересекается — вопрос со свободными окнами из ответа инструмента
def _conflict(
    call: FunctionCall, data: Mapping[str, object], turn: TurnInput, state: _Turn, started: float
) -> TurnResult:
    titles = [
        str(item["title"])
        for item in _list(data.get("conflicts"))
        if isinstance(item, dict) and item.get("title")
    ]
    busy = ", ".join(f"«{t}»" for t in titles) or "другая встреча"
    slots = [slot for raw in _list(data.get("free_slots")) if (slot := _slot(raw)) is not None]
    base = {k: v for k, v in call.arguments.items() if k not in {"starts_at", "ends_at"}}
    clarify = Clarify(
        missing=["starts_at"],
        options=[_slotLabel(slot, turn.tz) for slot in slots[:MAX_OPTIONS]],
        pending={
            "kind": "call",
            "tool": call.name,
            "args": base,
            "choices": [dict(slot) for slot in slots[:MAX_OPTIONS]],
        },
    )
    say = (ASK_SLOT if slots else NO_SLOTS).format(busy=busy)
    return _result("clarify", state, started, say=say, clarify=clarify)


# Примечание: вызов модели и ответ на него — в диалог, чтобы следующий шаг видел результат
# GigaChat принимает результат функции только JSON-объектом в строке (спецификация API)
def _answerModel(state: _Turn, reply: LlmReply, payload: JsonObject) -> None:
    call = reply.functionCall
    if call is None:
        return
    state.messages.append(
        LlmMessage(role="assistant", content="", functionCall=call, stateId=reply.stateId)
    )
    content = json.dumps(payload, ensure_ascii=False)
    state.messages.append(LlmMessage(role="function", content=content, name=call.name))


def _hasMutation(actions: Sequence[ExecutedAction]) -> bool:
    return any(a.name in MUTATING_TOOLS and a.result.ok for a in actions)


# Примечание: незавершённое действие для промпта: вызов и уже известные аргументы, чтобы ответ
# текстом («в 15») дополнил их, а не начал запись заново; свободный вопрос — его текст
def _pendingText(pending: JsonObject | None) -> str:
    if not pending:
        return NOTHING
    if pending.get("kind") == "call":
        args = json.dumps(pending.get("args") or {}, ensure_ascii=False)
        return f"вызов {pending.get('tool')} с аргументами {args}, не хватает ответа пользователя"
    question = pending.get("question")
    if not isinstance(question, str) or not question:
        return NOTHING
    request = pending.get(REQUEST_KEY)
    if isinstance(request, str) and request:
        # ответ («01.05», «утро») дополняет эту просьбу: создать запись, а не разбирать заново
        return f"задан вопрос «{question}» к просьбе «{request}»; ответ дополняет её"
    return f"задан вопрос «{question}»"


# Примечание: модели — summary инструмента; при ошибке — код и текст, чтобы она исправилась
def _forModel(result: ToolResult) -> JsonObject:
    if result.error is None:
        return {"result": result.summary}
    payload: JsonObject = {"error": result.error.code, "message": result.error.message}
    if result.error.details:
        payload["details"] = result.error.details
    return payload


# Примечание: код ошибки последнего вызова — им заканчивается ход, где ни один вызов не удался
def _lastToolCode(actions: Sequence[ExecutedAction]) -> ToolErrorCode:
    error = actions[-1].result.error
    return _toolCode(error.code if error is not None else "internal")


# Примечание: незнакомый код инструмента — internal: агент рисует только коды стандарта
def _toolCode(code: str) -> ToolErrorCode:
    return cast("ToolErrorCode", code if code in _TOOL_ERRORS else "internal")


def _error(code: BrainErrorCode | ToolErrorCode, state: _Turn, started: float) -> TurnResult:
    return TurnResult(
        outcome="error", actions=state.actions, errorCode=code, usage=_usage(state, started)
    )


# Примечание: успешный итог; фокус — записи из ответов инструментов этого хода
def _result(
    outcome: Literal["done", "answer", "clarify"],
    state: _Turn,
    started: float,
    *,
    say: str | None = None,
    clarify: Clarify | None = None,
    focus: Sequence[uuid.UUID] = (),
) -> TurnResult:
    found: list[uuid.UUID] = []
    for action in state.actions:
        if action.result.ok:
            found.extend(i for i in _itemIds(action.result.data) if i not in found)
    return TurnResult(
        outcome=outcome,
        actions=state.actions,
        say=say,
        clarify=clarify,
        focus=found or list(focus),
        usage=_usage(state, started),
    )


def _usage(state: _Turn, started: float) -> Usage:
    return Usage(
        model=state.model,
        llmCalls=state.llmCalls,
        tokensIn=state.tokensIn,
        tokensOut=state.tokensOut,
        latencyMs=round((time.perf_counter() - started) * 1000),
    )


# Примечание: id записей из data инструмента: item, items, results[].item (формы J3)
def _itemIds(data: Mapping[str, object] | None) -> list[uuid.UUID]:
    if not data:
        return []
    raw: list[object] = []
    if isinstance(item := data.get("item"), dict):
        raw.append(item.get("id"))
    raw.extend(i.get("id") for i in _list(data.get("items")) if isinstance(i, dict))
    raw.extend(
        r["item"].get("id")
        for r in _list(data.get("results"))
        if isinstance(r, dict) and isinstance(r.get("item"), dict)
    )
    ids: list[uuid.UUID] = []
    for value in raw:
        try:
            ids.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return ids


# Примечание: свободное окно {starts_at, ends_at}; не по форме — None
def _slot(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    start, end = raw.get("starts_at"), raw.get("ends_at")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    return {"starts_at": start, "ends_at": end}


# Примечание: подпись окна «16:00–17:00» в поясе пользователя
def _slotLabel(slot: Mapping[str, str], tz: str) -> str:
    zone = ZoneInfo(tz)
    start = datetime.fromisoformat(slot["starts_at"]).astimezone(zone)
    end = datetime.fromisoformat(slot["ends_at"]).astimezone(zone)
    return f"{start:%H:%M}–{end:%H:%M}"


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _strings(value: object) -> list[str]:
    return [v.strip() for v in _list(value) if isinstance(v, str) and v.strip()]
