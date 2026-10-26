"""LlmBrain.run на FakeLlmClient и фейковом ToolExecutor: сценарии ТЗ, уточнения и защиты."""

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

import pytest

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
    result = await subject.run(turnInput("Напомни завтра в 10 утра позвонить Ивану"), tools)
    assert result.outcome == "done"
    assert tools.calls == [
        ("create_task", {"title": "Позвонить Ивану", "kind": "reminder", "remind_at": REMIND_AT})
    ]
    assert result.focus == [ITEM_ID]
    assert (result.usage.llmCalls, result.usage.tokensIn, result.usage.tokensOut) == (2, 150, 15)
    second = llm.calls[1].messages
    # GigaChat принимает результат функции только JSON-объектом в строке
    assert (second[-1].role, second[-1].content) == (
        "function",
        '{"result": "Создано напоминание"}',
    )


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
    result = await subject.run(turnInput("напомни через 2 часа позвонить"), tools)
    assert result.outcome == "done"
    assert '"error": "past_time"' in llm.calls[1].messages[-1].content


# Примечание: вторая ошибка инструмента подряд — ход заканчивается её кодом
async def test_toolErrorTwice() -> None:
    subject, _ = brain(
        call("create_task", title="A", kind="reminder", remind_at="2026-10-24T10:00:00+03:00"),
        call("create_task", title="A", kind="reminder", remind_at="2026-10-24T11:00:00+03:00"),
    )
    tools = FakeTools({"create_task": [failed("past_time"), failed("past_time")]})
    result = await subject.run(turnInput("напомни сегодня утром позвонить"), tools)
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
    subject, _ = brain(text("Записал, сделаю"))
    result = await subject.run(turnInput("надо купить хлеб"), FakeTools())
    assert (result.outcome, result.errorCode, result.say) == ("error", "llm_no_action", None)


# Примечание: вызов с ошибкой, повтор отсечён как дубль, модель молчит — код ошибки, а не «Готово.»
async def test_failedCallThenSilence() -> None:
    noTitle = call("create_event", starts_at="2026-10-25T12:00:00+03:00")
    subject, _ = brain(noTitle, noTitle, text(""))
    tools = FakeTools({"create_event": [failed("validation_error")]})
    result = await subject.run(turnInput("назначь завтра в 13:00 встречу с Петром"), tools)
    assert (result.outcome, result.errorCode, result.say) == ("error", "validation_error", None)


# Примечание: «Готово» после вызова с ошибкой — тоже ложное «записал»
async def test_successClaimAfterFailedCall() -> None:
    subject, _ = brain(call("create_event", starts_at="2026-10-25T12:00:00+03:00"), text("Готово"))
    tools = FakeTools({"create_event": [failed("validation_error")]})
    result = await subject.run(turnInput("назначь завтра в 13:00 встречу с Петром"), tools)
    assert (result.outcome, result.errorCode, result.say) == ("error", "llm_no_action", None)


# Примечание: «удали», «отмени» — update_item и complete_item модели не показываем:
# GigaChat выбирал их вместо delete_item
@pytest.mark.parametrize("phrase", ["Удали встречу с Петром", "Отмени встречу с Петром"])
async def test_deleteRequestHidesUpdateAndComplete(phrase: str) -> None:
    subject, llm = brain(text("Какую запись удалить?"))
    await subject.run(turnInput(phrase), FakeTools())
    names = {tool.name for tool in llm.calls[0].tools}
    assert "delete_item" in names
    assert not names & {"update_item", "complete_item"}


# Примечание: «сделал» — только complete_item: GigaChat удалял запись вместо отметки
@pytest.mark.parametrize(
    "phrase",
    [
        "Я сходил на встречу с Петром",
        "Отметь выполненной",
        "я помыл машину",
        "я уже купила хлеб",
        "уже позвонил Ивану",
    ],
)
async def test_doneRequestHidesUpdateAndDelete(phrase: str) -> None:
    subject, llm = brain(text("Какую запись отметить?"))
    await subject.run(turnInput(phrase), FakeTools())
    names = {tool.name for tool in llm.calls[0].tools}
    assert "complete_item" in names
    assert not names & {"update_item", "delete_item"}


# Примечание: «я хотел …», «я забыл …» — не отметка сделанного: перенос и удаление доступны
@pytest.mark.parametrize("phrase", ["я хотел бы перенести встречу", "я забыл про созвон"])
async def test_wishIsNotDone(phrase: str) -> None:
    subject, llm = brain(text("Какую запись?"))
    await subject.run(turnInput(phrase), FakeTools())
    assert "update_item" in {tool.name for tool in llm.calls[0].tools}


# Примечание: «в 5» без «утра/вечера» — вопрос с двумя кнопками, вызова нет; кнопка сразу
# выполнит вызов с выбранным моментом (pending call)
async def test_ambiguousHourAsks() -> None:
    subject, _ = brain(
        call(
            "create_task", title="Позвонить", kind="reminder", remind_at="2026-10-24T17:00:00+03:00"
        )
    )
    tools = FakeTools()
    result = await subject.run(turnInput("напомни в 5 позвонить"), tools)
    assert (result.outcome, result.say, tools.calls) == (
        "clarify",
        "Утра или вечера: 05:00 или 17:00?",
        [],
    )
    assert result.clarify is not None
    assert result.clarify.options == ["05:00", "17:00"]
    assert result.clarify.pending["choices"] == [
        {"remind_at": "2026-10-25T05:00:00+03:00"},
        {"remind_at": "2026-10-24T17:00:00+03:00"},
    ]


# Примечание: время названо ясно — момент считает код: модель ошиблась днём, код — сегодня 17:00
async def test_clearTimeOverridesModel() -> None:
    subject, _ = brain(
        call(
            "create_event",
            title="Созвон",
            starts_at="2026-10-25T17:00:00+03:00",
            ends_at="2026-10-25T18:00:00+03:00",
        ),
        text("Готово"),
    )
    tools = FakeTools({"create_event": [CREATED]})
    await subject.run(turnInput("сегодня в 17:00 созвон с Петром"), tools)
    assert tools.calls == [
        (
            "create_event",
            {
                "title": "Созвон",
                "starts_at": "2026-10-24T17:00:00+03:00",
                "ends_at": "2026-10-24T18:00:00+03:00",
            },
        )
    ]


# Примечание: день назван ясно — дату и время оставляем как есть
async def test_namedDayKeepsModelTime() -> None:
    subject, _ = brain(
        call("create_task", title="Позвонить", kind="reminder", remind_at=REMIND_AT), text("Ок")
    )
    tools = FakeTools({"create_task": [CREATED]})
    await subject.run(turnInput("напомни завтра в 10 утра позвонить"), tools)
    assert tools.calls[0][1]["remind_at"] == REMIND_AT


# Примечание: встреча без дня — «На какой день?», сказанное время сохраняется в pending
async def test_eventWithoutDayAsksDay() -> None:
    subject, _ = brain(
        call("create_event", title="Зубмастер", starts_at="2026-10-25T10:15:00+03:00")
    )
    tools = FakeTools()
    result = await subject.run(turnInput("на десять пятнадцать"), tools)
    assert (result.outcome, result.say, tools.calls) == (
        "clarify",
        "На какой день поставить встречу?",
        [],
    )
    assert result.clarify is not None
    assert result.clarify.pending["said_time"] == {"hour": 10, "minute": 15, "is_clear": False}


# Примечание: «в 19:00 встреча с Павлом» → «На какой день?» → «сегодня» словом: встреча создаётся
# кодом из вопроса, без модели (модель читала «сегодня» как «что у меня сегодня»)
async def test_dayAnswerCreatesEventWithoutModel() -> None:
    subject, llm = brain(
        call("create_event", title="Встреча с Павлом", starts_at="2026-10-24T19:00:00+03:00")
    )
    asked = await subject.run(turnInput("в 19:00 встреча с Павлом"), FakeTools())
    assert asked.clarify is not None
    assert asked.clarify.options == ["Сегодня", "Завтра", "Послезавтра"]
    created = ok("Создано", {"status": "created", "item": item()})
    tools = FakeTools({"create_event": [created]})
    result = await subject.run(turnInput("сегодня", pending=asked.clarify.pending, focus=[]), tools)
    assert result.outcome == "done"
    assert tools.calls == [
        (
            "create_event",
            {"title": "Встреча с Павлом", "starts_at": "2026-10-24T19:00:00+03:00"},
        )
    ]
    assert len(llm.calls) == 1


# Примечание: прошедшее «сегодня» в варианты не попадает
async def test_dayQuestionSkipsPastToday() -> None:
    subject, _ = brain(
        call("create_event", title="Встреча с Павлом", starts_at="2026-10-24T09:00:00+03:00")
    )
    asked = await subject.run(turnInput("в 09:00 встреча с Павлом"), FakeTools())
    assert asked.clarify is not None
    assert asked.clarify.options == ["Завтра", "Послезавтра"]


# Примечание: «купить медведя» → «На какой день поставить задачу?» → «на завтра»: задача на завтра
async def test_taskDayAnswerCreatesTask() -> None:
    subject, _ = brain(call("create_task", title="купить медведя", kind="task"))
    asked = await subject.run(turnInput("купить медведя"), FakeTools())
    assert (asked.outcome, asked.say) == ("clarify", "На какой день поставить задачу?")
    assert asked.clarify is not None
    created = ok("Создано", {"status": "created", "item": item(kind="task")})
    tools = FakeTools({"create_task": [created]})
    result = await subject.run(turnInput("на завтра", pending=asked.clarify.pending), tools)
    assert result.outcome == "done"
    assert tools.calls == [
        ("create_task", {"title": "купить медведя", "kind": "task", "starts_at": "2026-10-25"})
    ]


# Примечание: несуществующая дата — вопрос, а не время 13:00
async def test_impossibleDateAsks() -> None:
    subject, _ = brain(
        call(
            "create_task",
            title="Съездить домой",
            kind="reminder",
            remind_at="2026-10-25T13:00:00+03:00",
        )
    )
    tools = FakeTools()
    result = await subject.run(turnInput("Мне нужно 01.13 съездить домой, напомни в обед"), tools)
    assert result.outcome == "clarify"
    assert result.say == "Такой даты нет: «01.13». Какую дату ты имел в виду?"
    assert tools.calls == []


# Примечание: перенос «без десяти три дня» — время кладётся в remind_at напоминания, даже если
# модель передала starts_at; принятый перенос с другим временем — не «Изменено»
async def test_rescheduleUsesReminderField() -> None:
    focus = [
        ItemBrief(
            id=ITEM_ID,
            kind="reminder",
            title="Тренировка",
            remindAt=datetime(2026, 10, 26, 2, 50, tzinfo=ZoneInfo(TZ)),
        )
    ]
    subject, _ = brain(
        call("update_item", id=str(ITEM_ID), starts_at="2026-10-26T14:50:00+03:00"), text("Ок")
    )
    moved = ok(
        "Изменено",
        {"status": "updated", "item": item(kind="reminder", remind_at="2026-10-26T14:50:00+03:00")},
    )
    tools = FakeTools({"update_item": [moved]})
    result = await subject.run(turnInput("без десяти три дня, исправь", focus=focus), tools)
    assert tools.calls == [
        ("update_item", {"id": str(ITEM_ID), "remind_at": "2026-10-26T14:50:00+03:00"})
    ]
    assert result.outcome == "done"


# Примечание: MCP принял перенос, а время у записи прежнее — не «Изменено»
async def test_timeNotAppliedIsNotDone() -> None:
    focus = [ItemBrief(id=ITEM_ID, kind="reminder", title="Тренировка")]
    subject, _ = brain(call("update_item", id=str(ITEM_ID), remind_at="2026-10-26T14:50:00+03:00"))
    same = ok(
        "Изменено",
        {"status": "updated", "item": item(kind="reminder", remind_at="2026-10-26T02:50:00+03:00")},
    )
    tools = FakeTools({"update_item": [same]})
    result = await subject.run(turnInput("перенеси на 14:50", focus=focus), tools)
    assert result.outcome == "answer"
    assert result.say is not None
    assert "Не получилось" in result.say


# Примечание: «когда у меня маникюр» — поиск, а не список за период
async def test_whenQuestionHidesList() -> None:
    subject, llm = brain(text("Ищу"))
    await subject.run(turnInput("когда у меня маникюо"), FakeTools())
    names = {tool.name for tool in llm.calls[0].tools}
    assert "search_items" in names
    assert "list_items" not in names


# Примечание: «напомни …» без времени, а модель создаёт задачу без момента — вопрос, без вызова
async def test_reminderWithoutTimeAsks() -> None:
    subject, _ = brain(call("create_task", title="Купить хлеб", kind="task"))
    tools = FakeTools()
    result = await subject.run(turnInput("напомни купить хлеб"), tools)
    assert (result.outcome, result.say, tools.calls) == ("clarify", "Во сколько напомнить?", [])
    assert result.clarify is not None
    assert result.clarify.options == ["10:00", "15:00", "19:00"]


# Примечание: «напомни об этой встрече за 40 минут» — встреча в фокусе: remind_at = начало − 40 мин
# считает код, без модели и без вопроса «Во сколько напомнить?»
async def test_remindBeforeFocusedEvent() -> None:
    subject, llm = brain()
    focus = [ItemBrief(id=ITEM_ID, kind="event", title="Суд", startsAt=NOW + timedelta(days=1))]
    updated = ok("Изменено", {"status": "updated", "item": item()})
    tools = FakeTools({"update_item": [updated]})
    result = await subject.run(turnInput("напомни об этой встрече за 40 минут", focus=focus), tools)
    assert result.outcome == "done"
    assert tools.calls == [
        ("update_item", {"id": str(ITEM_ID), "remind_at": "2026-10-25T11:20:00+03:00"})
    ]
    assert llm.calls == []


# Примечание: «напомни за 40 минут до встречи» без имени — встреча из фокуса, а не первая найденная
@pytest.mark.parametrize(
    "phrase", ["напомни за 40 минут до встречи", "напомни о встрече за 40 минут"]
)
async def test_remindBeforeUnnamedMeetingUsesFocus(phrase: str) -> None:
    subject, llm = brain()
    focus = [
        ItemBrief(id=ITEM_ID, kind="event", title="Чепецкий", startsAt=NOW + timedelta(days=1))
    ]
    updated = ok("Изменено", {"status": "updated", "item": item()})
    tools = FakeTools({"update_item": [updated]})
    result = await subject.run(turnInput(phrase, focus=focus), tools)
    assert result.outcome == "done"
    assert tools.calls == [
        ("update_item", {"id": str(ITEM_ID), "remind_at": "2026-10-25T11:20:00+03:00"})
    ]
    assert llm.calls == []


# Примечание: встреча не в фокусе — модель находит её, remind_at всё равно считает код от начала
# найденной встречи; время встречи не трогается
async def test_remindBeforeFoundEvent() -> None:
    subject, _ = brain(
        call("search_items", query="встреча с Иваном"),
        call("update_item", id=str(ITEM_ID), starts_at="2026-10-25T14:00:00+03:00"),
        text("Готово"),
    )
    found = item(starts_at="2026-10-25T15:00:00+03:00")
    hits = ok("1 запись", {"results": [{"item": found, "score": 0.9}]})
    updated = ok("Изменено", {"status": "updated", "item": item()})
    tools = FakeTools({"search_items": [hits], "update_item": [updated]})
    result = await subject.run(turnInput("напомни за час до встречи с Иваном"), tools)
    assert result.outcome == "done"
    assert tools.calls[1] == (
        "update_item",
        {"id": str(ITEM_ID), "remind_at": "2026-10-25T14:00:00+03:00"},
    )


LISTED: Final = ok(
    "2 записи",
    {
        "items": [item(), item(OTHER_ID, title="Сдать отчёт", kind="task")],
        "range": {"from": "2026-10-26", "to": "2026-11-02"},
        "truncated": False,
    },
)
DELETED: Final = ok("Удалено", {"status": "deleted", "item": item()})


# Примечание: «отмени все планы на неделю» — модели только list_items; по списку — вопрос
# «Удалить 2 записи…?» с кнопкой, id в pending, ничего не удалено
async def test_bulkDeleteAsks() -> None:
    subject, llm = brain()
    tools = FakeTools({"list_items": [LISTED]})
    result = await subject.run(turnInput("Отмени все планы на неделю"), tools)
    # период понятен коду (эта неделя, пн–вс) — список без модели
    assert llm.calls == []
    assert tools.calls == [("list_items", {"from": "2026-10-19", "to": "2026-10-26"})]
    assert result.outcome == "clarify"
    assert result.say == "Удалить 2 записи: «Созвон с Иваном», «Сдать отчёт»?"
    assert result.clarify is not None
    assert result.clarify.options == ["Да, удалить"]
    assert result.clarify.pending == {"kind": "delete_many", "ids": [str(ITEM_ID), str(OTHER_ID)]}


# Примечание: «Удали планы на сегодня» (без «все») — тоже всё за период; период непонятен коду —
# модели остаётся только list_items
async def test_bulkDeletePluralAndModelPeriod() -> None:
    today = ok("1 запись", {"items": [item()], "range": {}, "truncated": False})
    subject, _ = brain()
    tools = FakeTools({"list_items": [today]})
    result = await subject.run(turnInput("Удали планы на сегодня"), tools)
    assert tools.calls == [("list_items", {"from": "2026-10-24", "to": "2026-10-25"})]
    assert result.outcome == "clarify"
    # период не назван — все записи (на 10 лет в обе стороны), тоже с подтверждением
    subject, llm = brain()
    tools = FakeTools({"list_items": [today]})
    result = await subject.run(turnInput("удали вообще все записи"), tools)
    assert (result.outcome, llm.calls) == ("clarify", [])
    assert tools.calls == [("list_items", {"from": "2016-10-26", "to": "2036-10-21"})]


# Примечание: «все напоминания» — удаляются только напоминания, даже если модель не задала вид
async def test_bulkDeleteFiltersKind() -> None:
    reminder = item(OTHER_ID, title="Выпить кофе", kind="reminder")
    listed = ok("2 записи", {"items": [item(), reminder], "range": {}, "truncated": False})
    subject, _ = brain(call("list_items", **{"from": "2026-10-24", "to": "2027-10-24"}))
    result = await subject.run(
        turnInput("Удали все напоминания"), FakeTools({"list_items": [listed]})
    )
    assert result.say == "Удалить 1 запись: «Выпить кофе»?"
    assert result.clarify is not None
    assert result.clarify.pending == {"kind": "delete_many", "ids": [str(OTHER_ID)]}


# Примечание: пустой период — обычный ответ со списком, без вопроса
async def test_bulkDeleteNothing() -> None:
    empty = ok("Записей нет", {"items": [], "range": {}, "truncated": False})
    subject, _ = brain(call("list_items", **{"from": "2026-10-26", "to": "2026-10-27"}))
    result = await subject.run(turnInput("удали всё на завтра"), FakeTools({"list_items": [empty]}))
    assert (result.outcome, result.clarify) == ("answer", None)


PENDING_DELETE: Final = {"kind": "delete_many", "ids": [str(ITEM_ID), str(OTHER_ID)]}


# Примечание: кнопка «Да, удалить» — delete_item по каждому id, без модели; итог done
async def test_bulkDeleteConfirmedByButton() -> None:
    subject, llm = brain()
    tools = FakeTools({"delete_item": [DELETED, DELETED]})
    result = await subject.resume(turnInput("Да, удалить", pending=PENDING_DELETE), "0", tools)
    assert result.outcome == "done"
    assert tools.calls == [
        ("delete_item", {"id": str(ITEM_ID)}),
        ("delete_item", {"id": str(OTHER_ID)}),
    ]
    assert llm.calls == []


# Примечание: ответ текстом: «да» — удалить, другое — ничего не трогать
@pytest.mark.parametrize(
    ("text", "deletes"), [("да", 2), ("Удаляй", 2), ("Все", 2), ("нет", 0), ("потом", 0)]
)
async def test_bulkDeleteAnsweredByText(text: str, deletes: int) -> None:
    subject, llm = brain()
    tools = FakeTools({"delete_item": [DELETED, DELETED]})
    result = await subject.run(turnInput(text, pending=PENDING_DELETE), tools)
    assert len(tools.calls) == deletes
    assert result.outcome == ("done" if deletes else "answer")
    assert llm.calls == []


# Примечание: без просьбы удалить все инструменты на месте
async def test_otherRequestKeepsUpdate() -> None:
    subject, llm = brain(text("Какую запись перенести?"))
    await subject.run(turnInput("Перенеси встречу с Петром на завтра"), FakeTools())
    assert "update_item" in {tool.name for tool in llm.calls[0].tools}


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


TIME_CHOICES: Final = ["2026-10-25T10:00:00+03:00", "2026-10-25T15:00:00+03:00"]
ASK_TIME: Final = call(
    ASK_USER,
    question="На какое время?",
    missing=["starts_at"],
    options=["10:00", "15:00"],
    tool="create_event",
    args={"title": "Встреча с Иваном"},
    field="starts_at",
    values=TIME_CHOICES,
)


# Примечание: сценарий плана: «встреча с Иваном завтра» → варианты → кнопка «15:00» →
# create_event без второго вызова модели
async def test_clarifyThenResumeWithoutLlm() -> None:
    subject, llm = brain(ASK_TIME)
    created = ok("Создана встреча", {"status": "created", "item": item()})
    tools = FakeTools({"create_event": [created]})
    asked = await subject.run(turnInput("встреча с Иваном завтра"), tools)
    assert asked.clarify is not None
    picked = turnInput("15:00", pending=asked.clarify.pending)
    result = await subject.resume(picked, "1", tools)
    assert result.outcome == "done"
    assert tools.calls == [
        ("create_event", {"title": "Встреча с Иваном", "starts_at": TIME_CHOICES[1]})
    ]
    assert (len(llm.calls), result.usage.llmCalls, result.focus) == (1, 0, [ITEM_ID])


# Примечание: выбранное окно снова занято — новый вопрос со свободными окнами
async def test_resumeConflictAgain() -> None:
    subject, _ = brain()
    pending = {
        "kind": "call",
        "tool": "create_event",
        "args": {"title": "Встреча"},
        "choices": [{"starts_at": TIME_CHOICES[0]}],
    }
    conflict = ok(
        "Пересечение",
        {
            "status": "conflict",
            "item": None,
            "conflicts": [{"id": str(OTHER_ID), "kind": "event", "title": "Планёрка"}],
            "free_slots": [],
        },
    )
    tools = FakeTools({"create_event": [conflict]})
    result = await subject.resume(turnInput("10:00", pending=pending), "0", tools)
    assert result.outcome == "clarify"
    assert result.say == (
        "В это время уже есть: «Планёрка», свободных окон рядом нет. Назови другое время."
    )


# Примечание: в resume модели нет — ошибка инструмента сразу заканчивает ход своим кодом
async def test_resumeToolError() -> None:
    subject, _ = brain()
    pending = {
        "kind": "call",
        "tool": "create_task",
        "args": {"title": "Позвонить", "kind": "reminder"},
        "choices": [{"remind_at": "2026-10-24T09:00:00+03:00"}],
    }
    tools = FakeTools({"create_task": [failed("past_time")]})
    result = await subject.resume(turnInput("9:00", pending=pending), "0", tools)
    assert (result.outcome, result.errorCode) == ("error", "past_time")


# Примечание: свободный вопрос — подпись кнопки уходит модели обычным ходом
async def test_resumeAskGoesToModel() -> None:
    subject, llm = brain(call("complete_item", id=str(ITEM_ID)), text("Отметил"))
    focus = [ItemBrief(id=ITEM_ID, kind="task", title="Купить хлеб")]
    done = ok("Выполнено", {"status": "completed", "item": item(kind="task")})
    tools = FakeTools({"complete_item": [done]})
    pending = {"kind": "ask", "question": "Какую запись?"}
    picked = turnInput("Купить хлеб", pending=pending, focus=focus)
    result = await subject.resume(picked, "0", tools)
    assert result.outcome == "done"
    assert llm.calls[0].messages[-1].content.endswith("\nКупить хлеб")


# Примечание: «сейчас» и «завтра» — рядом с фразой: по датам из истории модель сдвигала «завтра»
async def test_todayNextToUserText() -> None:
    subject, llm = brain(text("Привет!"))
    await subject.run(turnInput("завтра в 11 встреча с Павлом"), FakeTools())
    assert llm.calls[0].messages[-1].content == (
        "(сейчас 2026-10-24 12:00, завтра 2026-10-25)\nзавтра в 11 встреча с Павлом"
    )


# Примечание: номер вне вариантов или pending не наш — просьба повторить, без вызовов
async def test_resumeBadChoice() -> None:
    subject, _ = brain()
    pending = {"kind": "call", "tool": "create_event", "args": {}, "choices": []}
    tools = FakeTools()
    result = await subject.resume(turnInput("10:00", pending=pending), "3", tools)
    assert (result.outcome, result.say, tools.calls) == (
        "answer",
        "Не понял выбор — повтори просьбу.",
        [],
    )


# Примечание: вариант ask_user с чужим id или временем без смещения кнопкой не выполнится —
# вопрос остаётся свободным
async def test_askUserUnsafeCallBecomesAsk() -> None:
    subject, _ = brain(
        call(
            ASK_USER,
            question="На когда перенести?",
            missing=["starts_at"],
            options=["10:00"],
            tool="update_item",
            args={"id": "deadbeef"},
            field="starts_at",
            values=["2026-10-25T10:00:00+03:00"],
        )
    )
    result = await subject.run(turnInput("перенеси созвон"), FakeTools())
    assert result.clarify is not None
    assert result.clarify.pending["kind"] == "ask"


# Примечание: модель сломалась после сохранения записи — ход всё равно done, а не ошибка
async def test_llmErrorAfterMutationIsDone() -> None:
    subject, _ = brain(
        call("create_task", title="Позвонить Ивану", kind="reminder", remind_at=REMIND_AT),
        LlmError("llm_bad_output", "422"),
    )
    tools = FakeTools({"create_task": [CREATED]})
    result = await subject.run(turnInput("Напомни завтра в 10 утра позвонить Ивану"), tools)
    assert (result.outcome, result.focus) == ("done", [ITEM_ID])


@dataclass
class SlowLlm:
    """Модель, которая думает дольше бюджета хода."""

    delayS: float

    async def complete(self, messages: object, tools: object, temperature: float = 0.0) -> LlmReply:
        await asyncio.sleep(self.delayS)
        return text("поздно")


# Примечание: модель не уложилась в бюджет хода — llm_timeout раньше таймаута агента
async def test_turnBudget() -> None:
    subject = LlmBrain(SlowLlm(delayS=5.0), model="GigaChat-2-Max", turnBudgetS=1.2)
    result = await subject.run(turnInput("привет"), FakeTools())
    assert (result.outcome, result.errorCode) == ("error", "llm_timeout")


# Примечание: «Готово» после поиска — не ложное «записал», а ответ с найденным
async def test_doneWordAfterSearch() -> None:
    subject, _ = brain(call("search_items", query="договор"), text("Готово, вот что нашёл"))
    hits = ok("1 запись", {"results": [{"item": item(), "score": 0.9}]})
    result = await subject.run(turnInput("найди договор"), FakeTools({"search_items": [hits]}))
    assert (result.outcome, result.focus) == ("answer", [ITEM_ID])


# Примечание: exclude_id проверяется так же, как id
async def test_unknownExcludeId() -> None:
    starts, ends = "2026-10-25T15:00:00+03:00", "2026-10-25T16:00:00+03:00"
    unknown = call("check_conflicts", starts_at=starts, ends_at=ends, exclude_id="deadbeef")
    subject, llm = brain(unknown, text("Свободно"))
    tools = FakeTools()
    await subject.run(turnInput("свободно ли завтра в 15"), tools)
    assert tools.calls == []
    assert "Сначала найди её" in llm.calls[1].messages[-1].content


# Примечание: на вопрос с кнопками ответили текстом — модель видит вызов и известные аргументы
async def test_waitingCallInPrompt() -> None:
    subject, llm = brain(text("Уточни"))
    pending = {
        "kind": "call",
        "tool": "create_event",
        "args": {"title": "Встреча с Иваном"},
        "choices": [{"starts_at": REMIND_AT}],
    }
    await subject.run(turnInput("в 15", pending=pending), FakeTools())
    system = llm.calls[0].messages[0].content
    assert 'create_event с аргументами {"title": "Встреча с Иваном"}' in system


# Примечание: functions_state_id из ответа модели уходит назад в сообщении assistant
async def test_stateIdKept() -> None:
    first = call("search_items", query="договор")
    withState = LlmReply(
        text="", functionCall=first.functionCall, model="GigaChat-2-Max", stateId="state-1"
    )
    subject, llm = brain(withState, text("Нашёл"))
    hits = ok("0 записей", {"results": []})
    await subject.run(turnInput("найди договор"), FakeTools({"search_items": [hits]}))
    assistant = llm.calls[1].messages[-2]
    assert (assistant.role, assistant.stateId) == ("assistant", "state-1")


# Примечание: встреча без времени — «Во сколько встреча?» без модели; день — в pending
async def test_eventWithoutTimeAsksBeforeModel() -> None:
    subject, llm = brain()
    result = await subject.run(turnInput("создай встречу с пингвином через неделю"), FakeTools())
    assert (result.outcome, result.say, llm.calls) == ("clarify", "Во сколько встреча?", [])
    assert result.clarify is not None
    assert result.clarify.pending["said_day"] == "2026-10-31"


# Примечание: несуществующая дата — вопрос до модели; ответ «01.05» и время из просьбы
async def test_impossibleDateBeforeModel() -> None:
    subject, llm = brain()
    result = await subject.run(turnInput("шашлыки 01.31"), FakeTools())
    assert (result.outcome, llm.calls) == ("clarify", [])
    assert result.say == "Такой даты нет: «01.31». Какую дату ты имел в виду?"


def asked(question: str, *answers: str) -> list[Turn]:
    at = NOW.isoformat()
    turns = [Turn.model_validate({"role": "user", "text": question, "at": at})]
    turns.extend(
        Turn.model_validate({"role": "assistant", "text": answer, "at": at}) for answer in answers
    )
    return turns


# Примечание: «Удали все планы» → вопрос модели о сроке → «Все» / «на завтра»: ответ дополняет
# просьбу — список за период кодом и «Удалить N?», без модели
@pytest.mark.parametrize(
    ("answer", "period"),
    [("Все", ("2016-10-26", "2036-10-21")), ("на завтра", ("2026-10-25", "2026-10-26"))],
)
async def test_bulkDeleteAnswerToPeriodQuestion(answer: str, period: tuple[str, str]) -> None:
    listed = ok("1 запись", {"items": [item()], "range": {}, "truncated": False})
    subject, llm = brain()
    tools = FakeTools({"list_items": [listed]})
    history = asked("Удали все планы", "Нужно уточнить период удаления. На какой срок удалить?")
    result = await subject.run(turnInput(answer, history=history), tools)
    assert (result.outcome, llm.calls) == ("clarify", [])
    assert tools.calls == [("list_items", {"from": period[0], "to": period[1]})]


# Примечание: модель спросила время текстом (без «?» в конце); ответ «15:00» — день из просьбы
async def test_dialogContextWithoutQuestionMark() -> None:
    subject, _ = brain(
        call("create_event", title="Встреча с пингвином", starts_at="2026-10-25T15:00:00+03:00"),
        text("Ок"),
    )
    tools = FakeTools({"create_event": [CREATED]})
    history = asked(
        "Создай встречу с пингвином через неделю",
        "На какое время назначить встречу с пингвином через неделю? "
        "Например, 10:00, 15:00 или 18:00.",
    )
    await subject.run(turnInput("15:00", history=history), tools)
    assert tools.calls[0][1]["starts_at"] == "2026-10-31T15:00:00+03:00"


# Примечание: новая просьба вместо ответа на «На какой день?» — старый вопрос закрыт, своя проверка
async def test_newRequestClosesPending() -> None:
    subject, llm = brain()
    pending = {"kind": "ask", "question": "На какой день поставить встречу?"}
    result = await subject.run(
        turnInput("Создай встречу с пингвином через неделю", pending=pending), FakeTools()
    )
    assert (result.outcome, result.say, llm.calls) == ("clarify", "Во сколько встреча?", [])
