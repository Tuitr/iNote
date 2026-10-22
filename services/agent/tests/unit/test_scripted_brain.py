from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

import pytest
import time_machine

from agent.infrastructure.brains import LLM_NOT_CONNECTED, BrainNotConnectedError, buildBrain
from agent.infrastructure.scripted_brain import (
    ASK_TIME,
    BAD_CHOICE,
    HELP,
    WRITE_TEXT,
    ScriptedBrain,
)
from contracts.agent_side import (
    ErrorInfo,
    ItemBrief,
    ToolResult,
    ToolSpec,
    TurnInput,
    TurnResult,
    UserSettings,
)
from services.agent.tests.fakes import ITEM, ITEM_ID

MSK: Final = "Europe/Moscow"
EKB: Final = "Asia/Yekaterinburg"


# Примечание: системные часы уводим в 2030 год — Brain обязан считать от turn.now
@pytest.fixture(autouse=True)
def wrongSystemClock() -> Iterator[None]:
    with time_machine.travel(datetime(2030, 1, 1, tzinfo=ZoneInfo("UTC")), tick=False):
        yield


# Примечание: исполнитель, который запоминает вызовы и отдаёт заданный результат
class RecordingTools:
    # Примечание: result — что вернуть на любой вызов
    def __init__(self, result: ToolResult | None = None) -> None:
        self.result = result or ToolResult(ok=True, summary="ok", data={"items": [ITEM]})
        self.calls: list[tuple[str, dict[str, object]]] = []

    # Примечание: ScriptedBrain схемы не читает
    async def listTools(self) -> list[ToolSpec]:
        return []

    # Примечание: запоминает имя и аргументы
    async def call(self, name: str, args: Mapping[str, object]) -> ToolResult:
        self.calls.append((name, dict(args)))
        return self.result


# Примечание: ход с «сейчас» в поясе пользователя
def turnAt(text: str, local: str, tz: str = MSK) -> TurnInput:
    now = datetime.fromisoformat(local).replace(tzinfo=ZoneInfo(tz))
    return TurnInput(text=text, inputType="text", now=now, tz=tz, settings=UserSettings(tz=tz))


# Примечание: прогон одной фразы — результат и вызовы
async def runPhrase(
    text: str, local: str, tz: str = MSK, tools: RecordingTools | None = None
) -> tuple[TurnResult, RecordingTools]:
    recorder = tools or RecordingTools()
    result = await ScriptedBrain().run(turnAt(text, local, tz), recorder)
    return result, recorder


# Примечание: периоды списка на краях: вечер воскресенья, 23:30, конец месяца и года, два пояса
@pytest.mark.parametrize(
    ("phrase", "local", "tz", "start", "end"),
    [
        (
            "Что у меня сегодня?",
            "2026-10-21T21:30",
            MSK,
            "2026-10-21T00:00:00+03:00",
            "2026-10-22T00:00:00+03:00",
        ),
        (
            "что у меня завтра",
            "2026-10-21T23:30",
            MSK,
            "2026-10-22T00:00:00+03:00",
            "2026-10-23T00:00:00+03:00",
        ),
        (
            "Что у меня завтра?",
            "2026-10-31T23:30",
            MSK,
            "2026-11-01T00:00:00+03:00",
            "2026-11-02T00:00:00+03:00",
        ),
        (
            "что у меня завтра",
            "2026-12-31T23:30",
            EKB,
            "2027-01-01T00:00:00+05:00",
            "2027-01-02T00:00:00+05:00",
        ),
        (
            "Что у меня на неделе?",
            "2026-10-25T21:30",
            MSK,
            "2026-10-19T00:00:00+03:00",
            "2026-10-26T00:00:00+03:00",
        ),
        (
            "что у меня на этой неделе",
            "2026-10-26T00:10",
            EKB,
            "2026-10-26T00:00:00+05:00",
            "2026-11-02T00:00:00+05:00",
        ),
        (
            "покажи дела на неделю",
            "2026-12-31T23:30",
            MSK,
            "2026-12-28T00:00:00+03:00",
            "2027-01-04T00:00:00+03:00",
        ),
        (
            "что у меня сегодня",
            "2026-10-22T01:00",
            EKB,
            "2026-10-22T00:00:00+05:00",
            "2026-10-23T00:00:00+05:00",
        ),
    ],
)
async def test_listPeriods(phrase: str, local: str, tz: str, start: str, end: str) -> None:
    result, tools = await runPhrase(phrase, local, tz)
    assert tools.calls == [("list_items", {"from": start, "to": end})]
    assert result.outcome == "answer"
    assert [str(i) for i in result.focus] == [ITEM_ID]


# Примечание: «напомни завтра в 10 …» → reminder с due_at и remind_at; регистр имён сохранён
@pytest.mark.parametrize(
    ("phrase", "local", "tz", "title", "moment"),
    [
        (
            "напомни завтра в 10 позвонить Ивану",
            "2026-10-21T21:30",
            MSK,
            "Позвонить Ивану",
            "2026-10-22T10:00:00+03:00",
        ),
        (
            "Напомни мне позвонить Ивану завтра в 9:30.",
            "2026-12-31T23:30",
            EKB,
            "Позвонить Ивану",
            "2027-01-01T09:30:00+05:00",
        ),
        (
            "напомни сегодня в 23 выключить духовку",
            "2026-10-31T21:00",
            MSK,
            "Выключить духовку",
            "2026-10-31T23:00:00+03:00",
        ),
    ],
)
async def test_remindWithTime(phrase: str, local: str, tz: str, title: str, moment: str) -> None:
    created = ToolResult(ok=True, summary="ok", data={"status": "created", "item": ITEM})
    result, tools = await runPhrase(phrase, local, tz, RecordingTools(created))
    expected = {"title": title, "kind": "reminder", "due_at": moment, "remind_at": moment}
    assert tools.calls == [("create_task", expected)]
    assert result.outcome == "done"
    assert result.actions[0].result.ok
    assert [str(i) for i in result.focus] == [ITEM_ID]


# Примечание: напоминание без времени — вопрос с вариантами 9/13/19 в будущем и свой pending
@pytest.mark.parametrize(
    ("phrase", "local", "tz", "labels", "first"),
    [
        (
            "напомни купить хлеб",
            "2026-10-21T21:30",
            MSK,
            ["завтра 9:00", "завтра 13:00", "завтра 19:00"],
            "2026-10-22T09:00:00+03:00",
        ),
        (
            "напомни купить хлеб",
            "2026-10-21T10:30",
            EKB,
            ["сегодня 13:00", "сегодня 19:00", "завтра 9:00"],
            "2026-10-21T13:00:00+05:00",
        ),
        (
            "напомни завтра позвонить маме",
            "2026-10-21T10:30",
            MSK,
            ["завтра 9:00", "завтра 13:00", "завтра 19:00"],
            "2026-10-22T09:00:00+03:00",
        ),
    ],
)
async def test_remindWithoutTimeAsks(
    phrase: str, local: str, tz: str, labels: list[str], first: str
) -> None:
    result, tools = await runPhrase(phrase, local, tz)
    assert tools.calls == []
    assert (result.outcome, result.say) == ("clarify", ASK_TIME)
    assert result.clarify is not None
    assert result.clarify.options == labels
    pending = result.clarify.pending
    assert pending["kind"] == "time"
    assert pending["field"] == "due_at"
    options = pending["options"]
    assert isinstance(options, list)
    assert options[0] == first
    assert "завтра" not in str(pending["args"])


# Примечание: выбор варианта → create_task на выбранное время, подтверждение — из ответа инструмента
async def test_resumeTimeCreatesReminder() -> None:
    asked, _ = await runPhrase("напомни купить хлеб", "2026-10-21T21:30")
    assert asked.clarify is not None
    created = ToolResult(ok=True, summary="ok", data={"status": "created", "item": ITEM})
    tools = RecordingTools(created)
    turn = turnAt("завтра 13:00", "2026-10-21T21:35").model_copy(
        update={"pending": asked.clarify.pending}
    )
    result = await ScriptedBrain().resume(turn, "1", tools)
    moment = "2026-10-22T13:00:00+03:00"
    assert tools.calls == [
        (
            "create_task",
            {"title": "Купить хлеб", "kind": "reminder", "due_at": moment, "remind_at": moment},
        )
    ]
    assert result.outcome == "done"


# Примечание: на вопрос о времени ответили текстом «в 15» — Brain понимает это при живом pending
@pytest.mark.parametrize(
    ("answer", "moment"),
    [("в 15", "2026-10-22T15:00:00+03:00"), ("сегодня в 23:15", "2026-10-21T23:15:00+03:00")],
)
async def test_timeAnsweredByText(answer: str, moment: str) -> None:
    asked, _ = await runPhrase("напомни купить хлеб", "2026-10-21T21:30")
    assert asked.clarify is not None
    tools = RecordingTools(
        ToolResult(ok=True, summary="ok", data={"status": "created", "item": ITEM})
    )
    turn = turnAt(answer, "2026-10-21T21:31").model_copy(update={"pending": asked.clarify.pending})
    result = await ScriptedBrain().run(turn, tools)
    assert result.outcome == "done"
    assert tools.calls[0][1]["due_at"] == moment


# Примечание: при живом pending новая просьба — обычная фраза, вопрос забывается
async def test_newPhraseIgnoresPending() -> None:
    turn = turnAt("что у меня завтра", "2026-10-21T21:31").model_copy(
        update={"pending": {"kind": "time", "tool": "create_task", "args": {}, "options": []}}
    )
    tools = RecordingTools()
    result = await ScriptedBrain().run(turn, tools)
    assert [name for name, _ in tools.calls] == ["list_items"]
    assert result.outcome == "answer"


# Примечание: ASR не уверен — «Я правильно понял?»; «Да» выполняет фразу, «Нет» — просит текст
async def test_lowConfidenceConfirm() -> None:
    turn = turnAt("напомни завтра в 10 позвонить Ивану", "2026-10-21T21:30").model_copy(
        update={"inputType": "voice", "asrLowConfidence": True}
    )
    tools = RecordingTools(
        ToolResult(ok=True, summary="ok", data={"status": "created", "item": ITEM})
    )
    asked = await ScriptedBrain().run(turn, tools)
    assert asked.outcome == "clarify"
    assert asked.say == "Я правильно понял: «напомни завтра в 10 позвонить Ивану»?"
    assert asked.clarify is not None
    assert asked.clarify.options == ["Да", "Нет, напишу текстом"]
    assert tools.calls == []
    answer = turn.model_copy(update={"text": "Да", "pending": asked.clarify.pending})
    yes = await ScriptedBrain().resume(answer, "0", tools)
    assert yes.outcome == "done"
    assert [name for name, _ in tools.calls] == ["create_task"]
    no = await ScriptedBrain().resume(answer, "1", tools)
    assert (no.outcome, no.say) == ("answer", WRITE_TEXT)


# Примечание: «встреча с … завтра в 15» → create_event с названием и временем начала
async def test_eventCreated() -> None:
    created = ToolResult(ok=True, summary="ok", data={"status": "created", "item": ITEM})
    result, tools = await runPhrase(
        "Встреча с Иваном завтра в 15", "2026-10-21T21:30", tools=RecordingTools(created)
    )
    assert tools.calls == [
        ("create_event", {"title": "Встреча с Иваном", "starts_at": "2026-10-22T15:00:00+03:00"})
    ]
    assert result.outcome == "done"


# Примечание: встреча без времени — тот же вопрос о времени, поле starts_at
async def test_eventWithoutTimeAsks() -> None:
    result, tools = await runPhrase("созвон с командой завтра", "2026-10-21T21:30")
    assert tools.calls == []
    assert result.clarify is not None
    assert result.clarify.pending["tool"] == "create_event"
    assert result.clarify.missing == ["starts_at"]


# Примечание: конфликт → вопрос со свободными слотами; выбор слота → create_event в этом слоте
async def test_conflictOffersSlots() -> None:
    busy = dict(ITEM, title="Стоматолог", kind="event")
    conflict = ToolResult(
        ok=True,
        summary="конфликт",
        data={
            "status": "conflict",
            "item": None,
            "conflicts": [busy],
            "free_slots": [
                {"starts_at": "2026-10-22T10:00:00+03:00", "ends_at": "2026-10-22T11:00:00+03:00"},
                {"starts_at": "2026-10-22T11:00:00+03:00", "ends_at": "2026-10-22T12:00:00+03:00"},
            ],
        },
    )
    asked, _ = await runPhrase(
        "встреча с Петром завтра в 9:30", "2026-10-21T21:30", tools=RecordingTools(conflict)
    )
    assert asked.outcome == "clarify"
    assert asked.say is not None
    assert "«Стоматолог»" in asked.say
    assert asked.clarify is not None
    assert asked.clarify.options == ["10:00–11:00", "11:00–12:00"]
    created = RecordingTools(
        ToolResult(ok=True, summary="ok", data={"status": "created", "item": ITEM})
    )
    turn = turnAt("11:00–12:00", "2026-10-21T21:31").model_copy(
        update={"pending": asked.clarify.pending}
    )
    done = await ScriptedBrain().resume(turn, "1", created)
    assert done.outcome == "done"
    assert created.calls == [
        (
            "create_event",
            {
                "title": "Встреча с Петром",
                "starts_at": "2026-10-22T11:00:00+03:00",
                "ends_at": "2026-10-22T12:00:00+03:00",
            },
        )
    ]


# Примечание: конфликт без свободных окон — ответ без записи, а не «готово»
async def test_conflictWithoutSlots() -> None:
    conflict = ToolResult(
        ok=True,
        summary="конфликт",
        data={"status": "conflict", "item": None, "conflicts": [ITEM], "free_slots": []},
    )
    result, _ = await runPhrase(
        "встреча с Петром завтра в 10", "2026-10-21T21:30", tools=RecordingTools(conflict)
    )
    assert result.outcome == "answer"
    assert result.say is not None
    assert "свободных окон" in result.say


# Примечание: выбор не из своих вариантов (чужой pending, номер вне списка) — просим повторить
@pytest.mark.parametrize(
    ("pending", "choice"),
    [(None, "0"), ({"kind": "time", "options": []}, "0"), ({"kind": "slot", "slots": []}, "x")],
)
async def test_badChoice(pending: dict[str, object] | None, choice: str) -> None:
    turn = turnAt("9:00", "2026-10-21T21:31").model_copy(update={"pending": pending})
    result = await ScriptedBrain().resume(turn, choice, RecordingTools())
    assert (result.outcome, result.say) == ("answer", BAD_CHOICE)


# Примечание: задача — с простым временем или вовсе без времени
@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("создай задачу купить хлеб", {"title": "Купить хлеб", "kind": "task"}),
        (
            "Добавь задачу завтра в 9 отправить отчёт",
            {"title": "Отправить отчёт", "kind": "task", "due_at": "2026-10-22T09:00:00+03:00"},
        ),
    ],
)
async def test_createTask(phrase: str, expected: dict[str, object]) -> None:
    created = ToolResult(ok=True, summary="ok", data={"status": "created", "item": ITEM})
    _, tools = await runPhrase(phrase, "2026-10-21T21:30", tools=RecordingTools(created))
    assert tools.calls == [("create_task", expected)]


# Примечание: «найди …» → search_items с запросом как есть и лимитом 5
async def test_search() -> None:
    found = ToolResult(
        ok=True,
        summary="ok",
        data={"results": [{"item": ITEM, "score": 0.9}], "best_match_confident": True},
    )
    result, tools = await runPhrase(
        "Найди договор с поставщиком", "2026-10-21T21:30", tools=RecordingTools(found)
    )
    assert tools.calls == [("search_items", {"query": "договор с поставщиком", "limit": 5})]
    assert result.outcome == "answer"
    assert [str(i) for i in result.focus] == [ITEM_ID]


# Примечание: непонятое — подсказка без вызова инструментов; caller Brain не формирует никогда
@pytest.mark.parametrize("phrase", ["привет", "удали всё", "создай задачу", ""])
async def test_unknownPhrase(phrase: str) -> None:
    text = phrase or " "
    turn = TurnInput(
        text=text,
        inputType="text",
        now=datetime.fromisoformat("2026-10-21T21:30:00+03:00"),
        tz=MSK,
        settings=UserSettings(tz=MSK),
    )
    tools = RecordingTools()
    result = await ScriptedBrain().run(turn, tools)
    assert tools.calls == []
    assert result.outcome == "answer"
    assert result.say
    if phrase == "привет":
        assert result.say == HELP


# Примечание: ошибка инструмента → outcome=error с кодом инструмента; незнакомый код → internal
@pytest.mark.parametrize(
    ("code", "expected"), [("past_time", "past_time"), ("weird_code", "internal")]
)
async def test_toolErrorBecomesError(code: str, expected: str) -> None:
    failed = ToolResult(ok=False, summary="Ошибка", error=ErrorInfo(code=code, message="x"))
    result, _ = await runPhrase(
        "напомни завтра в 10 позвонить", "2026-10-21T21:30", tools=RecordingTools(failed)
    )
    assert result.outcome == "error"
    assert result.errorCode == expected
    error = result.actions[0].result.error
    assert error is not None
    assert error.code == code


# Примечание: аргументы инструментов — без caller и user_id
async def test_noCallerInArgs() -> None:
    _, tools = await runPhrase("что у меня сегодня", "2026-10-21T21:30")
    assert all("caller" not in args and "user_id" not in args for _, args in tools.calls)


# Примечание: AGENT_BRAIN=llm — понятная ошибка до мержа с B
async def test_llmMode() -> None:
    assert isinstance(buildBrain("scripted"), ScriptedBrain)
    with pytest.raises(BrainNotConnectedError, match="AGENT_BRAIN=scripted"):
        buildBrain("llm")
    assert "libs/brain" in LLM_NOT_CONNECTED


# Примечание: запись в фокусе (после «Изменить время») + ответ временем → update_item по её id;
# «перенеси на …» — то же; без фокуса или с двумя записями — обычный разбор
@pytest.mark.parametrize(
    ("phrase", "focus", "expected"),
    [
        ("завтра в 15", 1, "2026-10-22T15:00:00+03:00"),
        ("Перенеси на завтра в 9:30", 1, "2026-10-22T09:30:00+03:00"),
        ("в 10", 1, "2026-10-22T10:00:00+03:00"),
        ("завтра в 15", 0, None),
        ("завтра в 15", 2, None),
    ],
)
async def test_rescheduleFocused(phrase: str, focus: int, expected: str | None) -> None:
    brief = ItemBrief.fromWire(ITEM)
    other = ItemBrief.fromWire({**ITEM, "id": "2b8d4e61-0c3f-4a7b-8e95-1f6a2d3c4b57"})
    turn = turnAt(phrase, "2026-10-21T21:30").model_copy(update={"focus": [brief, other][:focus]})
    tools = RecordingTools(
        ToolResult(ok=True, summary="ok", data={"status": "updated", "item": ITEM})
    )
    result = await ScriptedBrain().run(turn, tools)
    if expected is None:
        assert tools.calls == []
        assert result.say == HELP
        return
    assert tools.calls == [("update_item", {"id": ITEM_ID, "starts_at": expected})]
    assert result.outcome == "done"
