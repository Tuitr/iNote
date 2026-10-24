"""LlmBrain.run на FakeLlmClient и фейковом ToolExecutor: сценарии ТЗ, уточнения и защиты."""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from brain.llm import FakeLlmClient, FunctionCall, LlmError, LlmReply
from brain.llm_brain import LlmBrain
from brain.prompt import ASK_USER
from contracts.agent_side import (
    ErrorInfo,
    ItemBrief,
    ToolResult,
    ToolSpec,
    Turn,
    TurnInput,
    UserSettings,
)

TZ: Final = "Europe/Moscow"
NOW: Final = datetime(2026, 10, 24, 12, 0, tzinfo=ZoneInfo(TZ))
ITEM_ID: Final = uuid.UUID("1f2e3d4c-0000-4000-8000-000000000001")
OTHER_ID: Final = uuid.UUID("9a8b7c6d-0000-4000-8000-000000000002")
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


@dataclass
class FakeTools:
    """ToolExecutor без MCP: ответы по имени инструмента по очереди, вызовы запоминаются."""

    responses: dict[str, list[ToolResult]] = field(default_factory=dict)
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    async def listTools(self) -> list[ToolSpec]:
        return [ToolSpec(name=n, description="", inputSchema={}) for n in TOOL_NAMES]

    async def call(self, name: str, args: Mapping[str, object]) -> ToolResult:
        self.calls.append((name, dict(args)))
        return self.responses[name].pop(0)


def turnInput(text: str, **extra: object) -> TurnInput:
    return TurnInput.model_validate(
        {
            "text": text,
            "inputType": "text",
            "now": NOW,
            "tz": TZ,
            "settings": UserSettings(tz=TZ),
            **extra,
        }
    )


def call(name: str, **arguments: object) -> LlmReply:
    return LlmReply(
        text="",
        functionCall=FunctionCall(name=name, arguments=arguments),
        model="GigaChat-2-Max",
        tokensIn=100,
        tokensOut=10,
    )


def text(value: str) -> LlmReply:
    return LlmReply(text=value, functionCall=None, model="GigaChat-2-Max", tokensIn=50, tokensOut=5)


def item(itemId: uuid.UUID = ITEM_ID, **fields: object) -> dict[str, object]:
    return {"id": str(itemId), "kind": "event", "title": "Созвон с Иваном", **fields}


def ok(summary: str, data: dict[str, object]) -> ToolResult:
    return ToolResult(ok=True, summary=summary, data=data)


def failed(code: str) -> ToolResult:
    return ToolResult(ok=False, summary=code, error=ErrorInfo(code=code, message="время прошло"))


def brain(*script: LlmReply | LlmError, maxSteps: int = 4) -> tuple[LlmBrain, FakeLlmClient]:
    llm = FakeLlmClient(script=list(script))
    return LlmBrain(llm, model="GigaChat-2-Max", maxSteps=maxSteps), llm


REMIND_AT: Final = "2026-10-25T10:00:00+03:00"
CREATED: Final = ok("Создано напоминание", {"status": "created", "item": item(kind="reminder")})


# Примечание: напоминание: вызов, summary модели, финальный текст — done по вызову, а не по тексту
async def test_reminderDone() -> None:
    subject, llm = brain(
        call("create_task", title="Позвонить Ивану", kind="reminder", remind_at=REMIND_AT),
        text("Готово"),
    )
    tools = FakeTools({"create_task": [CREATED]})
    result = await subject.run(turnInput("Напомни завтра в 10 позвонить Ивану"), tools)
    assert result.outcome == "done"
    assert tools.calls == [
        ("create_task", {"title": "Позвонить Ивану", "kind": "reminder", "remind_at": REMIND_AT})
    ]
    assert result.focus == [ITEM_ID]
    assert (result.usage.llmCalls, result.usage.tokensIn, result.usage.tokensOut) == (2, 150, 15)
    second = llm.calls[1].messages
    assert (second[-1].role, second[-1].content) == ("function", "Создано напоминание")


# Примечание: встреча создаётся тем же циклом
async def test_eventDone() -> None:
    subject, _ = brain(
        call("create_event", title="Встреча с Петром", starts_at="2026-10-25T15:00:00+03:00"),
        text("Встреча создана"),
    )
    tools = FakeTools(
        {"create_event": [ok("Создана встреча", {"status": "created", "item": item()})]}
    )
    result = await subject.run(turnInput("Встреча с Петром завтра в 15"), tools)
    assert result.outcome == "done"
    assert [a.name for a in result.actions] == ["create_event"]


# Примечание: список за период — answer сразу после инструмента, без второго вызова модели
async def test_listAnswersWithoutSecondCall() -> None:
    subject, llm = brain(call("list_items", **{"from": "2026-10-25", "to": "2026-10-26"}))
    listed = ok("1 запись", {"items": [item()], "range": {}, "truncated": False})
    result = await subject.run(turnInput("что у меня завтра"), FakeTools({"list_items": [listed]}))
    assert (result.outcome, result.focus, len(llm.calls)) == ("answer", [ITEM_ID], 1)


# Примечание: поиск по смыслу — answer с текстом модели и найденными записями в фокусе
async def test_searchAnswer() -> None:
    subject, _ = brain(call("search_items", query="договор"), text("Нашёл одну запись"))
    hits = ok("1 запись", {"results": [{"item": item(), "score": 0.9}]})
    result = await subject.run(turnInput("найди договор"), FakeTools({"search_items": [hits]}))
    assert (result.outcome, result.say, result.focus) == ("answer", "Нашёл одну запись", [ITEM_ID])


# Примечание: встреча без времени — ask_user с вызовом: варианты и pending для resume без модели
async def test_askUserWithCall() -> None:
    subject, _ = brain(
        call(
            ASK_USER,
            question="На какое время?",
            missing=["starts_at"],
            options=["10:00", "15:00"],
            tool="create_event",
            args={"title": "Встреча с Иваном"},
            field="starts_at",
            values=["2026-10-25T10:00:00+03:00", "2026-10-25T15:00:00+03:00"],
        )
    )
    tools = FakeTools()
    result = await subject.run(turnInput("встреча с Иваном завтра"), tools)
    assert result.outcome == "clarify"
    assert result.clarify is not None
    assert (result.say, result.clarify.options) == ("На какое время?", ["10:00", "15:00"])
    assert result.clarify.pending == {
        "kind": "call",
        "tool": "create_event",
        "args": {"title": "Встреча с Иваном"},
        "choices": [
            {"starts_at": "2026-10-25T10:00:00+03:00"},
            {"starts_at": "2026-10-25T15:00:00+03:00"},
        ],
    }
    assert tools.calls == []


# Примечание: ask_user без вызова (непонятно, о какой записи) — pending «ask», ответ пойдёт модели
async def test_askUserFreeQuestion() -> None:
    subject, _ = brain(
        call(ASK_USER, question="Какую запись?", missing=["id"], options=["Первую", "Вторую"])
    )
    focus = [ItemBrief(id=ITEM_ID, kind="task", title="Купить хлеб")]
    result = await subject.run(turnInput("удали её", focus=focus), FakeTools())
    assert result.clarify is not None
    assert result.clarify.pending == {"kind": "ask", "question": "Какую запись?"}
    assert result.focus == [ITEM_ID]


# Примечание: конфликт встреч — вопрос со свободными окнами; вызов в actions (кнопка «Всё равно»)
async def test_conflictAsksSlot() -> None:
    starts = "2026-10-25T15:00:00+03:00"
    subject, llm = brain(call("create_event", title="Встреча с Петром", starts_at=starts))
    conflict = ok(
        "Пересечение",
        {
            "status": "conflict",
            "item": None,
            "conflicts": [{"id": str(OTHER_ID), "kind": "event", "title": "Планёрка"}],
            "free_slots": [
                {"starts_at": "2026-10-25T16:00:00+03:00", "ends_at": "2026-10-25T17:00:00+03:00"}
            ],
        },
    )
    result = await subject.run(
        turnInput("встреча с Петром завтра в 15"), FakeTools({"create_event": [conflict]})
    )
    assert result.outcome == "clarify"
    assert result.clarify is not None
    assert result.say == "В это время уже есть: «Планёрка». Выбери свободное время:"
    assert result.clarify.options == ["16:00–17:00"]
    assert result.clarify.pending["args"] == {"title": "Встреча с Петром"}
    assert [a.name for a in result.actions] == ["create_event"]
    assert len(llm.calls) == 1


# Примечание: «перенеси её» — короткий id из фокуса принимается
async def test_moveFocusedByShortId() -> None:
    subject, llm = brain(
        call("update_item", id=str(ITEM_ID)[:8], starts_at="2026-10-26T15:00:00+03:00"),
        text("Перенёс"),
    )
    focus = [ItemBrief(id=ITEM_ID, kind="event", title="Созвон с Иваном", startsAt=NOW)]
    moved = ok("Перенесено", {"status": "updated", "item": item()})
    tools = FakeTools({"update_item": [moved]})
    result = await subject.run(turnInput("перенеси её на послезавтра в 15", focus=focus), tools)
    assert result.outcome == "done"
    assert f"- {str(ITEM_ID)[:8]} встреча «Созвон с Иваном»" in llm.calls[0].messages[0].content


# Примечание: id не из фокуса — вызов не выполняется, модель идёт искать; id из поиска принимается
async def test_unknownIdSearchFirst() -> None:
    subject, llm = brain(
        call("delete_item", id="deadbeef"),
        call("search_items", query="созвон с Иваном"),
        call("delete_item", id=str(ITEM_ID)),
        text("Удалил"),
    )
    hits = ok("1 запись", {"results": [{"item": item(), "score": 0.9}]})
    deleted = ok("Удалено", {"status": "deleted", "item": item()})
    tools = FakeTools({"search_items": [hits], "delete_item": [deleted]})
    result = await subject.run(turnInput("удали созвон с Иваном"), tools)
    assert result.outcome == "done"
    assert [name for name, _ in tools.calls] == ["search_items", "delete_item"]
    assert "Сначала найди её через search_items" in llm.calls[1].messages[-1].content


# Примечание: время без смещения — одна подсказка; повтор той же ошибки — llm_bad_output
async def test_naiveTimeOneHint() -> None:
    naive = call("create_task", title="Позвонить", kind="reminder", remind_at="2026-10-25T10:00")
    subject, llm = brain(naive, naive)
    tools = FakeTools()
    result = await subject.run(turnInput("напомни завтра в 10 позвонить"), tools)
    assert (result.outcome, result.errorCode, tools.calls) == ("error", "llm_bad_output", [])
    assert "без смещения" in llm.calls[1].messages[-1].content


# Примечание: validation_error / past_time — модель видит ошибку и исправляет аргументы один раз
async def test_toolErrorRetriedOnce() -> None:
    subject, llm = brain(
        call(
            "create_task", title="Позвонить", kind="reminder", remind_at="2026-10-24T10:00:00+03:00"
        ),
        call("create_task", title="Позвонить", kind="reminder", remind_at=REMIND_AT),
        text("Готово"),
    )
    tools = FakeTools({"create_task": [failed("past_time"), CREATED]})
    result = await subject.run(turnInput("напомни в 10 позвонить"), tools)
    assert result.outcome == "done"
    assert '"error": "past_time"' in llm.calls[1].messages[-1].content


# Примечание: вторая ошибка инструмента подряд — ход заканчивается её кодом
async def test_toolErrorTwice() -> None:
    subject, _ = brain(
        call("create_task", title="A", kind="reminder", remind_at="2026-10-24T10:00:00+03:00"),
        call("create_task", title="A", kind="reminder", remind_at="2026-10-24T11:00:00+03:00"),
    )
    tools = FakeTools({"create_task": [failed("past_time"), failed("past_time")]})
    result = await subject.run(turnInput("напомни сегодня в 10"), tools)
    assert (result.outcome, result.errorCode, len(result.actions)) == ("error", "past_time", 2)


# Примечание: not_found и незнакомые коды не повторяются; незнакомый код — internal
async def test_toolErrorNotRetried() -> None:
    subject, _ = brain(call("complete_item", id=str(ITEM_ID)))
    focus = [ItemBrief(id=ITEM_ID, kind="task", title="Купить хлеб")]
    tools = FakeTools({"complete_item": [failed("weird_code")]})
    result = await subject.run(turnInput("отметь её", focus=focus), tools)
    assert (result.outcome, result.errorCode) == ("error", "internal")


# Примечание: «Записал» без изменяющего вызова — llm_no_action, текст пользователю не уходит
async def test_successClaimWithoutCall() -> None:
    subject, _ = brain(text("Записал, напомню завтра"))
    result = await subject.run(turnInput("напомни завтра позвонить"), FakeTools())
    assert (result.outcome, result.errorCode, result.say) == ("error", "llm_no_action", None)


# Примечание: простой вопрос — ответ текстом без инструментов
async def test_plainAnswer() -> None:
    subject, _ = brain(text("Привет! Чем помочь?"))
    result = await subject.run(turnInput("привет"), FakeTools())
    assert (result.outcome, result.say) == ("answer", "Привет! Чем помочь?")


# Примечание: пустой ответ модели без вызовов — llm_bad_output
async def test_emptyReply() -> None:
    subject, _ = brain(text(""))
    result = await subject.run(turnInput("привет"), FakeTools())
    assert result.errorCode == "llm_bad_output"


# Примечание: одинаковый вызов второй раз не выполняется
async def test_duplicateCollapsed() -> None:
    search = call("search_items", query="договор")
    subject, _ = brain(search, search, text("Вот что нашёл"))
    hits = ok("1 запись", {"results": [{"item": item(), "score": 0.9}]})
    tools = FakeTools({"search_items": [hits]})
    result = await subject.run(turnInput("найди договор"), tools)
    assert (result.outcome, len(tools.calls)) == ("answer", 1)


# Примечание: лимит шагов — не больше maxSteps вызовов модели; итог по уже сделанному
async def test_stepLimit() -> None:
    subject, llm = brain(*[call("search_items", query=f"запрос {i}") for i in range(5)], maxSteps=3)
    hits = ok("0 записей", {"results": []})
    tools = FakeTools({"search_items": [hits, hits, hits]})
    result = await subject.run(turnInput("найди что-нибудь"), tools)
    assert (result.outcome, len(llm.calls), len(tools.calls)) == ("answer", 3, 3)


# Примечание: ошибка LLM — outcome=error с её кодом, исключение наружу не выходит
async def test_llmError() -> None:
    subject, _ = brain(LlmError("llm_timeout", "slow"))
    result = await subject.run(turnInput("привет"), FakeTools())
    assert (result.outcome, result.errorCode) == ("error", "llm_timeout")
    assert (result.usage.model, result.usage.llmCalls) == ("GigaChat-2-Max", 0)


# Примечание: модели уходят инструменты и ask_user, история и фраза — отдельными сообщениями
async def test_messagesAndTools() -> None:
    subject, llm = brain(text("Привет"))
    history = [
        Turn(role="user", text="что у меня завтра", at=NOW),
        Turn(role="assistant", text="Завтра пусто", at=NOW),
    ]
    await subject.run(turnInput("спасибо", history=history), FakeTools())
    sent = llm.calls[0]
    assert [m.role for m in sent.messages] == ["system", "user", "assistant", "user"]
    assert "Сейчас: 2026-10-24T12:00+03:00 (суббота)" in sent.messages[0].content
    assert [t.name for t in sent.tools][-1] == ASK_USER
    assert len(sent.tools) == len(TOOL_NAMES) + 1
