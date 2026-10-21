from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

import pytest
import time_machine

from agent.infrastructure.brains import LLM_NOT_CONNECTED, BrainNotConnectedError, buildBrain
from agent.infrastructure.scripted_brain import ASK_TIME, HELP, ScriptedBrain, periodRange
from contracts.agent_side import (
    ErrorInfo,
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


# Примечание: один и тот же момент UTC — разные «сегодня» в Москве и Екатеринбурге
def test_sameInstantDifferentToday() -> None:
    instant = datetime.fromisoformat("2026-10-21T20:30:00+00:00")
    msk = periodRange("today", instant, MSK)
    ekb = periodRange("today", instant, EKB)
    assert msk.start.date().isoformat() == "2026-10-21"
    assert ekb.start.date().isoformat() == "2026-10-22"


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


# Примечание: напоминание без времени не создаём — просим время (уточнение — S2)
async def test_remindWithoutTimeAsks() -> None:
    result, tools = await runPhrase("напомни купить хлеб", "2026-10-21T21:30")
    assert tools.calls == []
    assert (result.outcome, result.say) == ("answer", ASK_TIME)


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


# Примечание: resume — S2; AGENT_BRAIN=llm — понятная ошибка до мержа с B
async def test_resumeAndLlmMode() -> None:
    with pytest.raises(NotImplementedError, match="S2"):
        await ScriptedBrain().resume(turnAt("10:00", "2026-10-21T21:30"), "10:00", RecordingTools())
    assert isinstance(buildBrain("scripted"), ScriptedBrain)
    with pytest.raises(BrainNotConnectedError, match="AGENT_BRAIN=scripted"):
        buildBrain("llm")
    assert "libs/brain" in LLM_NOT_CONNECTED
