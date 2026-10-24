"""LlmBrain: ход пользователя через модель с инструментами (реализация Brain из J2).

Цикл: модель → вызов инструмента через ToolExecutor → summary назад модели → снова модель,
не больше maxSteps вызовов модели. Итог определяет код, а не текст модели: done — был успешный
изменяющий вызов, answer — только чтение или текст, clarify — ask_user или конфликт встреч,
error — код LLM или инструмента. Защиты держит код: повтор того же вызова не выполняется,
id записи — только известный, время без смещения и validation_error / past_time — одна попытка
исправиться, «записал» без изменяющего вызова — llm_no_action.
resume — ответ кнопкой на уточнение: вызов из pending достраивается выбранным вариантом без модели.
"""

import json
import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Literal, cast, get_args
from zoneinfo import ZoneInfo

from brain.llm import FunctionCall, JsonObject, LlmClient, LlmError, LlmMessage, LlmReply
from brain.prompt import ASK_USER, PROMPT_VERSION, buildSystemPrompt
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
# Изменяющие инструменты J3: только их успешный вызов даёт outcome=done
MUTATING_TOOLS: Final = frozenset(
    {"create_task", "create_event", "update_item", "complete_item", "delete_item"}
)
# После списка модели нечего делать: агент рисует его из data, второй вызов модели — лишние секунды
FINAL_TOOLS: Final = frozenset({"list_items"})
TIME_FIELDS: Final = ("starts_at", "ends_at", "remind_at")
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
            "args": {"type": "object", "description": "Уже известные аргументы этой функции"},
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


# Примечание: Brain на LLM; клиент модели (GigaChat или фейк) выбирает агент
class LlmBrain:
    def __init__(self, llm: LlmClient, model: str, maxSteps: int = DEFAULT_MAX_STEPS) -> None:
        self._llm = llm
        self._model = model
        self._maxSteps = maxSteps

    # Примечание: новый ход: цикл модели с инструментами; исключения LLM наружу не выходят
    async def run(self, turn: TurnInput, tools: ToolExecutor) -> TurnResult:
        started = time.perf_counter()
        specs = await tools.listTools()
        state = _Turn(
            messages=buildMessages(turn),
            knownIds={str(b.id) for b in turn.focus},
            model=self._model,
        )
        try:
            result = await self._loop(turn, tools, [*specs, ASK_USER_SPEC], state, started)
        except LlmError as exc:
            result = _error(exc.code, state, started)
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
        tool, args = pending.get("tool"), pending.get("args")
        choices = [c for c in _list(pending.get("choices")) if isinstance(c, dict)]
        index = int(choice) if choice.isdigit() else -1
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
                _answerModel(state, call, text)
                continue
            if _isDuplicate(call, state.actions):
                _answerModel(state, call, HINT_DUPLICATE)
                continue
            result = await tools.call(call.name, call.arguments)
            state.actions.append(ExecutedAction(name=call.name, args=call.arguments, result=result))
            if (stop := _afterCall(call, result, turn, state, started)) is not None:
                return stop
            _answerModel(state, call, _forModel(result))
        return _final("", state, started)

    async def _complete(self, state: _Turn, specs: Sequence[ToolSpec]) -> LlmReply:
        reply = await self._llm.complete(state.messages, specs)
        state.llmCalls += 1
        state.tokensIn += reply.tokensIn
        state.tokensOut += reply.tokensOut
        state.model = reply.model or state.model
        return reply


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
    system = buildSystemPrompt(turn.now, turn.tz)
    if turn.focus:
        lines = [_focusLine(brief, turn.tz) for brief in turn.focus]
        system += "\n\nЗаписи в фокусе (о них «она», «эту», «первую»):\n" + "\n".join(lines)
    if turn.asrLowConfidence:
        system += "\n\nФраза распознана из голоса неуверенно: если смысл неясен, спроси ask_user."
    history = [LlmMessage(role=t.role, content=t.text) for t in turn.history]
    return [
        LlmMessage(role="system", content=system),
        *history,
        LlmMessage(role="user", content=turn.text),
    ]


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
    itemId = call.arguments.get("id")
    if itemId is not None and not _isKnownId(itemId, state.knownIds):
        return "unknown_id", HINT_UNKNOWN_ID.format(value=itemId)
    for name in TIME_FIELDS:
        value = call.arguments.get(name)
        if isinstance(value, str) and _NAIVE_TIME_RE.match(value):
            return "offset", HINT_OFFSET.format(value=value)
    return None


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
    state.knownIds.update(str(i) for i in _itemIds(result.data))
    data = result.data or {}
    if data.get("status") == "conflict":
        return _conflict(call, data, turn, state, started)
    if call.name in FINAL_TOOLS:
        return _result("answer", state, started)
    return None


# Примечание: модель закончила текстом: итог по вызовам, а не по словам модели
def _final(text: str, state: _Turn, started: float) -> TurnResult:
    if any(a.name in MUTATING_TOOLS and a.result.ok for a in state.actions):
        return _result("done", state, started)
    if _SUCCESS_CLAIM_RE.search(text):
        return _error("llm_no_action", state, started)
    if state.actions:
        return _result("answer", state, started, say=text.strip() or None)
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
def _answerModel(state: _Turn, call: FunctionCall, content: str) -> None:
    state.messages.append(LlmMessage(role="assistant", content="", functionCall=call))
    state.messages.append(LlmMessage(role="function", content=content, name=call.name))


# Примечание: модели — summary инструмента; при ошибке — код и текст, чтобы она исправилась
def _forModel(result: ToolResult) -> str:
    if result.error is None:
        return result.summary
    payload = {"error": result.error.code, "message": result.error.message}
    if result.error.details:
        payload["details"] = json.dumps(result.error.details, ensure_ascii=False)
    return json.dumps(payload, ensure_ascii=False)


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
