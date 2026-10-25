"""time_guard на примерах из отчёта о тестах (понедельник 26.10.2026, ночь) и похожих случаях."""

import uuid
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from brain.llm import FunctionCall, JsonObject
from brain.time_guard import Ask, guardTime, timeNotApplied
from contracts.agent_side import ItemBrief, ToolResult, TurnInput, UserSettings

TZ: Final = "Europe/Moscow"
NIGHT: Final = datetime(2026, 10, 26, 1, 41, tzinfo=ZoneInfo(TZ))
ITEM_ID: Final = uuid.UUID("1f2e3d4c-0000-4000-8000-000000000001")


def turn(
    text: str, pending: JsonObject | None = None, focus: list[ItemBrief] | None = None
) -> TurnInput:
    return TurnInput.model_validate(
        {
            "text": text,
            "inputType": "voice",
            "now": NIGHT,
            "tz": TZ,
            "settings": UserSettings(tz=TZ),
            "pending": pending,
            "focus": focus or [],
        }
    )


def event(**args: object) -> FunctionCall:
    return FunctionCall("create_event", {"title": "Встреча с зубмастером", **args})


def reminder(**args: object) -> FunctionCall:
    return FunctionCall("create_task", {"title": "Съездить", "kind": "reminder", **args})


def asked(result: FunctionCall | Ask) -> Ask:
    assert isinstance(result, Ask)
    return result


def called(result: FunctionCall | Ask) -> dict[str, object]:
    assert isinstance(result, FunctionCall)
    return dict(result.arguments)


# 1. Встреча без дня: модель спросила только время, на «на десять пятнадцать» поставила «завтра»
def test_eventWithoutDayAsksDayAndKeepsTime() -> None:
    pending: JsonObject = {
        "kind": "ask",
        "question": "На какое время поставить встречу с зубмастером?",
    }
    ask = asked(
        guardTime(
            event(starts_at="2026-10-27T10:15:00+03:00"), turn("на десять пятнадцать", pending)
        )
    )
    assert ask.question == "На какой день поставить встречу?"
    assert ask.options == ["Сегодня", "Завтра", "Послезавтра"]
    # ответ «послезавтра»: день из ответа, сказанное время из pending — оно неясное, вопрос
    then = asked(
        guardTime(event(starts_at="2026-10-28T10:15:00+03:00"), turn("послезавтра", ask.pending))
    )
    assert then.question == "Утра или вечера: 10:15 или 22:15?"
    assert then.pending["choices"] == [
        {"starts_at": "2026-10-28T10:15:00+03:00"},
        {"starts_at": "2026-10-28T22:15:00+03:00"},
    ]


# 1. Встреча с днём и ясным временем — без вопросов
def test_eventWithDayAndClearTime() -> None:
    args = called(
        guardTime(event(starts_at="2026-10-27T10:00:00+03:00"), turn("встреча послезавтра в 15:00"))
    )
    assert args["starts_at"] == "2026-10-28T15:00:00+03:00"


# 3. «Давай без десяти 3» на «На какое время послезавтра…?» — утра или вечера, день из вопроса
def test_beforeTenThreeAsksHalfDay() -> None:
    pending: JsonObject = {
        "kind": "ask",
        "question": "На какое время послезавтра поставить напоминание съездить на тренировку?",
    }
    ask = asked(
        guardTime(
            reminder(remind_at="2026-10-28T02:50:00+03:00"), turn("Давай без десяти 3", pending)
        )
    )
    assert ask.question == "Утра или вечера: 02:50 или 14:50?"
    assert ask.pending["kind"] == "call"
    assert ask.pending["choices"] == [
        {"remind_at": "2026-10-28T02:50:00+03:00"},
        {"remind_at": "2026-10-28T14:50:00+03:00"},
    ]


# 3. «Без десяти три дня, исправь»: перенос напоминания — remind_at, даже если модель дала starts_at
def test_rescheduleReminderField() -> None:
    focus = [
        ItemBrief(
            id=ITEM_ID,
            kind="reminder",
            title="Съездить на тренировку",
            remindAt=datetime(2026, 10, 28, 2, 50, tzinfo=ZoneInfo(TZ)),
        )
    ]
    call = FunctionCall(
        "update_item", {"id": str(ITEM_ID)[:8], "starts_at": "2026-10-28T02:50:00+03:00"}
    )
    args = called(guardTime(call, turn("Без десяти три дня, исправь", focus=focus)))
    assert args == {"id": str(ITEM_ID)[:8], "remind_at": "2026-10-28T14:50:00+03:00"}


# 3. Принятый перенос, а время у записи другое — не «Изменено»
def test_timeNotApplied() -> None:
    call = FunctionCall("update_item", {"id": "1f2e3d4c", "remind_at": "2026-10-28T14:50:00+03:00"})
    same = ToolResult(
        ok=True, summary="", data={"item": {"remind_at": "2026-10-28T02:50:00+03:00"}}
    )
    moved = ToolResult(
        ok=True, summary="", data={"item": {"remind_at": "2026-10-28T11:50:00+00:00"}}
    )
    assert timeNotApplied(call, same) is True
    assert timeNotApplied(call, moved) is False


# 4. «ближе к 12 ночи следующего дня» в понедельник 01:46 — вторник, время модели
def test_nextDayIsTuesday() -> None:
    call = reminder(title="Почистить зубы", remind_at="2026-10-28T23:50:00+03:00")
    args = called(
        guardTime(call, turn("Поставь ближе к 12 ночи следующего дня напоминание почистить зубы"))
    )
    assert args["remind_at"] == "2026-10-27T23:50:00+03:00"


# 5. «01.13» — не время 13:00, а несуществующая дата
def test_impossibleDate() -> None:
    call = reminder(remind_at="2026-10-27T13:00:00+03:00")
    ask = asked(guardTime(call, turn("Мне нужно 01.13 съездить домой, напомни в обед")))
    assert ask.question == "Такой даты нет: «01.13». Какую дату ты имел в виду?"


# 6. «1 мая поездку на шашлыки» — задача с датой 1 мая 2027, а не «без срока»
def test_wordDateKept() -> None:
    call = FunctionCall("create_task", {"title": "поездка на шашлыки", "kind": "task"})
    args = called(guardTime(call, turn("Запланируй 1 мая поездку на шашлыки")))
    assert args["starts_at"] == "2027-05-01"


# 7. «в мае 03.06.2027» — противоречие, вопрос с двумя вариантами
def test_monthConflict() -> None:
    call = event(title="поездка на шашлыки", starts_at="2027-06-03T10:00:00+03:00")
    ask = asked(guardTime(call, turn("Запланируй мне поездку на шашлыки в мае 03.06.2027 в 10:00")))
    assert ask.question == "Уточни дату: в мае или чт 3 июня?"
    assert ask.options == ["в мае", "чт 3 июня"]


# Время ясно, день не назван: напоминание — сегодня, если впереди, иначе завтра
def test_clearTimeWithoutDay() -> None:
    args = called(
        guardTime(reminder(remind_at="2026-10-27T15:30:00+03:00"), turn("напомни в 15:30"))
    )
    assert args["remind_at"] == "2026-10-26T15:30:00+03:00"
    early = called(
        guardTime(reminder(remind_at="2026-10-26T01:00:00+03:00"), turn("напомни в 01:00"))
    )
    assert early["remind_at"] == "2026-10-27T01:00:00+03:00"


# Не про время — вызов как есть
def test_otherToolsUntouched() -> None:
    call = FunctionCall("search_items", {"query": "маникюр"})
    assert guardTime(call, turn("когда у меня маникюр в 5")) is call


# 3. «Напомни послезавтра съездить на тренировку» — модель придумала 00:00: спросить время,
# день сохраняется в pending; ответ «без десяти 3» — утра или вечера на тот же день
def test_inventedTimeAsksTime() -> None:
    ask = asked(
        guardTime(
            reminder(remind_at="2026-10-28T00:00:00+03:00"),
            turn("Напомни мне послезавтра съездить на тренировку"),
        )
    )
    assert (ask.question, ask.pending["said_day"]) == ("Во сколько напомнить?", "2026-10-28")
    then = asked(
        guardTime(
            reminder(remind_at="2026-10-28T02:50:00+03:00"), turn("без десяти 3", ask.pending)
        )
    )
    assert then.question == "Утра или вечера: 02:50 или 14:50?"
    assert then.pending["choices"] == [
        {"remind_at": "2026-10-28T02:50:00+03:00"},
        {"remind_at": "2026-10-28T14:50:00+03:00"},
    ]


# «утром», «вечером» без часа — это время, вопроса нет
def test_partOfDayIsTime() -> None:
    call = reminder(remind_at="2026-10-26T19:00:00+03:00")
    assert (
        called(guardTime(call, turn("напомни вечером купить молоко")))["remind_at"]
        == "2026-10-26T19:00:00+03:00"
    )


# Модель передала время напоминания в оба поля — остаётся только remind_at
def test_reminderBothFields() -> None:
    focus = [ItemBrief(id=ITEM_ID, kind="reminder", title="Тренировка")]
    moment = "2026-10-28T14:50:00+03:00"
    call = FunctionCall(
        "update_item", {"id": str(ITEM_ID), "remind_at": moment, "starts_at": moment}
    )
    args = called(guardTime(call, turn("Без десяти три дня, исправь", focus=focus)))
    assert args == {"id": str(ITEM_ID), "remind_at": moment}
