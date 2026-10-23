"""Шаблоны ответа: подтверждения и списки из данных инструментов, даты, markdown, 4000 символов."""

import tomllib
import uuid
from datetime import UTC, date, datetime, timedelta
from importlib import resources
from typing import Final

import pytest
from pydantic import ValidationError
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.json import JSONSnapshotExtension

from agent.application.ports import Notice
from agent.domain.user import UserContext
from agent.presentation.dates import DateFormatter
from agent.presentation.markdown import escapeMarkdown
from agent.presentation.reply_renderer import TemplateReplyRenderer
from agent.presentation.templates import REQUIRED_ERROR_KEYS, ReplyTemplates, loadTemplates
from contracts.agent_side import Clarify, ExecutedAction, ToolResult, TurnResult
from contracts.reply import REPLY_TEXT_MAX_LENGTH, ReplyState
from services.agent.tests.fakes import USAGE, FixedClock

TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
# Среда 21.10.2026, 21:30 в Москве (18:30 UTC)
NOW: Final = datetime(2026, 10, 21, 18, 30, tzinfo=UTC)
MSK: Final = "Europe/Moscow"
EKB: Final = "Asia/Yekaterinburg"

type Json = dict[str, object]


# Примечание: снапшоты Reply в JSON — читаемый дифф при правке шаблона
@pytest.fixture
def jsonSnapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    return snapshot.use_extension(JSONSnapshotExtension)


# Примечание: рендер в поясе tz при «сейчас» = now
def render(result: TurnResult, tz: str = MSK, now: datetime = NOW) -> Json:
    user = UserContext(userId=17, maxUserId=1, maxChatId=2, tz=tz)
    reply = TemplateReplyRenderer(loadTemplates(), FixedClock(now)).render(result, user, TRACE)
    return reply.toWire()


# Примечание: запись по форме J3; id детерминирован от названия
def item(title: str, startsAt: str | None, **extra: object) -> Json:
    itemId = uuid.uuid5(uuid.NAMESPACE_URL, title)
    allDay = extra.pop("all_day", startsAt is None)
    return {
        "id": str(itemId),
        "kind": extra.pop("kind", "task"),
        "title": title,
        "starts_at": startsAt,
        "all_day": allDay,
        "status": "open",
        **extra,
    }


# Примечание: выполненный вызов инструмента с ответом data
def action(name: str, data: Json, args: Json | None = None) -> ExecutedAction:
    return ExecutedAction(
        name=name, args=args or {}, result=ToolResult(ok=True, summary="ok", data=data)
    )


# Примечание: итог хода
def turn(outcome: str, *actions: ExecutedAction, **extra: object) -> TurnResult:
    return TurnResult.model_validate(
        {"outcome": outcome, "actions": list(actions), "usage": USAGE, **extra}
    )


# Примечание: создание записи — ответ create_task / create_event
def created(record: Json, tool: str = "create_task") -> ExecutedAction:
    return action(tool, {"status": "created", "item": record, "conflicts": [], "free_slots": []})


# Примечание: list_items за [start, end) с записями
def listed(start: str, end: str, *records: Json) -> ExecutedAction:
    data: Json = {"items": list(records), "range": {"from": start, "to": end}, "truncated": False}
    return action("list_items", data, {"from": start, "to": end})


REMINDER: Final = item("Позвонить Ивану", "2026-10-22T10:00:00+03:00", kind="reminder")
EVENT: Final = item(
    "Созвон с командой",
    "2026-10-21T10:00:00+03:00",
    kind="event",
    ends_at="2026-10-21T11:00:00+03:00",
    all_day=False,
)
BREAD: Final = item("Купить хлеб", "2026-10-21T00:00:00+03:00", all_day=True)
TODAY: Final = ("2026-10-21T00:00:00+03:00", "2026-10-22T00:00:00+03:00")


# Примечание: подтверждение — из сохранённой записи, а не из say модели (правило 3)
@pytest.mark.parametrize("tz", [MSK, EKB], ids=["msk", "ekb"])
def test_confirmReminder(tz: str, jsonSnapshot: SnapshotAssertion) -> None:
    reply = render(turn("done", created(REMINDER), say="Записал на послезавтра!"), tz)
    assert "послезавтра" not in str(reply["text"])
    assert reply == jsonSnapshot


# Примечание: задача без срока, встреча с концом, конфликт и два создания сразу
@pytest.mark.parametrize(
    "case",
    [
        "task_without_date",
        "event",
        "conflict",
        "two_created",
        "completed",
    ],
)
def test_confirmVariants(case: str, jsonSnapshot: SnapshotAssertion) -> None:
    actions = {
        "task_without_date": [created(item("Купить хлеб", None))],
        "event": [created(EVENT | {"starts_at": "2026-10-23T15:00:00+03:00"}, "create_event")],
        "conflict": [
            action(
                "create_event",
                {
                    "status": "conflict",
                    "item": None,
                    "conflicts": [EVENT],
                    "free_slots": [
                        {
                            "starts_at": "2026-10-21T11:00:00+03:00",
                            "ends_at": "2026-10-21T12:00:00+03:00",
                        }
                    ],
                },
            )
        ],
        "two_created": [created(REMINDER), created(item("Купить хлеб", None))],
        "completed": [
            action("complete_item", {"status": "completed", "item": REMINDER | {"status": "done"}})
        ],
    }[case]
    assert render(turn("done", *actions)) == jsonSnapshot


# Примечание: список за сегодня: время, интервал, «весь день»; кнопки «Завтра», «Неделя», день
def test_listToday(jsonSnapshot: SnapshotAssertion) -> None:
    reminder = item("Позвонить Ивану", "2026-10-21T18:00:00+03:00", kind="reminder", all_day=False)
    reply = render(turn("answer", listed(*TODAY, reminder, EVENT, BREAD), say="У тебя три дела"))
    assert str(reply["text"]).startswith("Сегодня, ср 21 октября:")
    assert reply == jsonSnapshot


# Примечание: пустой день, неделя с понедельника, произвольный период через границу месяцев
@pytest.mark.parametrize(
    ("case", "start", "end"),
    [
        ("tomorrow_empty", "2026-10-22T00:00:00+03:00", "2026-10-23T00:00:00+03:00"),
        ("week", "2026-10-19T00:00:00+03:00", "2026-10-26T00:00:00+03:00"),
        ("week_two_months", "2026-10-26T00:00:00+03:00", "2026-11-02T00:00:00+03:00"),
        ("range", "2026-10-30T00:00:00+03:00", "2026-11-03T00:00:00+03:00"),
    ],
    ids=["tomorrow_empty", "week", "week_two_months", "range"],
)
def test_listPeriods(case: str, start: str, end: str, jsonSnapshot: SnapshotAssertion) -> None:
    records: list[Json] = []
    if case != "tomorrow_empty":
        records = [
            item("Стоматолог", start.replace("00:00", "09:30"), kind="event", all_day=False),
            item(
                "Отчёт",
                (datetime.fromisoformat(start) + timedelta(days=2)).isoformat(),
                all_day=True,
            ),
        ]
    assert render(turn("answer", listed(start, end, *records))) == jsonSnapshot


# Примечание: поиск: одна уверенная запись, несколько похожих, ничего
@pytest.mark.parametrize("case", ["one", "similar", "empty"])
def test_search(case: str, jsonSnapshot: SnapshotAssertion) -> None:
    results = {
        "one": [{"item": EVENT, "score": 0.9}],
        "similar": [{"item": EVENT, "score": 0.6}, {"item": REMINDER, "score": 0.55}],
        "empty": [],
    }[case]
    data: Json = {"results": results, "best_match_confident": case == "one"}
    assert render(turn("answer", action("search_items", data, {"query": "созвон"}))) == jsonSnapshot


# Примечание: уточнение — вопрос модели и варианты кнопками по 3 в ряд
def test_clarify(jsonSnapshot: SnapshotAssertion) -> None:
    clarify = Clarify(
        missing=["starts_at"], options=["09:00", "13:00", "19:00", "завтра"], pending={}
    )
    reply = render(turn("clarify", say="На какое время?", clarify=clarify))
    assert reply["state"] == "clarify"
    assert reply == jsonSnapshot


# Примечание: свободный ответ — текст модели как есть, без разметки; без текста — «Готово.»
def test_answerText() -> None:
    reply = render(turn("answer", say="*Привет*! Чем помочь?"))
    assert (reply["text"], reply["format"]) == ("*Привет*! Чем помочь?", "plain")
    assert render(turn("answer"))["text"] == "Готово."


# Примечание: у каждого кода — свой текст и короткий код цепочки; незнакомый код — общий текст
@pytest.mark.parametrize("code", sorted(REQUIRED_ERROR_KEYS - {"code", "default"}))
def test_errorTexts(code: str) -> None:
    reply = render(turn("error", errorCode=code))
    templates = loadTemplates()
    assert reply["state"] == "error"
    assert reply["text"] == f"{templates.errors[code]}\nКод: ab12f0"
    renderer = TemplateReplyRenderer(templates, FixedClock(NOW))
    assert renderer.failure("something_new", TRACE).text.startswith(templates.errors["default"])


# Примечание: названия экранируются — пользователь не ломает разметку ответа
def test_markdownEscapedInTitles() -> None:
    evil = item("*Срочно* [ссылка](http://x) _план_", "2026-10-21T12:00:00+03:00", all_day=False)
    reply = render(turn("answer", listed(*TODAY, evil)))
    assert r"\*Срочно\* \[ссылка\]\(http://x\) \_план\_" in str(reply["text"])
    assert reply["format"] == "markdown"


# Примечание: длинный список укладывается в 4000 символов, хвост — «…и ещё N», счёт сходится
def test_longListFitsLimit() -> None:
    records = [
        item(
            f"Дело номер {i} " + "очень длинное название " * 5,
            "2026-10-21T12:00:00+03:00",
            all_day=False,
        )
        for i in range(300)
    ]
    text = str(render(turn("answer", listed(*TODAY, *records)))["text"])
    assert len(text) <= REPLY_TEXT_MAX_LENGTH
    lines = text.split("\n")
    shown = len(lines) - 2  # заголовок и «…и ещё N»
    assert lines[-1] == f"…и ещё {300 - shown}"


# Примечание: вечер воскресенья: «завтра» — понедельник следующей недели
def test_sundayEveningTomorrow() -> None:
    sundayNight = datetime(2026, 10, 25, 20, 30, tzinfo=UTC)  # 23:30 вс в Москве
    monday = item("Планёрка", "2026-10-26T10:00:00+03:00", kind="event", all_day=False)
    reply = render(turn("done", created(monday, "create_event")), MSK, sundayNight)
    assert "Завтра, пн 26 октября, 10:00" in str(reply["text"])


# Примечание: 23:30 в Москве — уже 01:30 четверга в Екатеринбурге: «сегодня» у каждого своё
def test_todayDependsOnTz() -> None:
    lateMsk = datetime(2026, 10, 21, 20, 30, tzinfo=UTC)
    record = item("Позвонить", "2026-10-22T10:00:00+03:00", kind="reminder", all_day=False)
    msk = str(render(turn("done", created(record)), MSK, lateMsk)["text"])
    ekb = str(render(turn("done", created(record)), EKB, lateMsk)["text"])
    assert "Завтра, чт 22 октября, 10:00" in msk
    assert "Сегодня, чт 22 октября, 12:00" in ekb


# Примечание: конец года: 31 декабря — «Завтра» и год у даты следующего года
def test_newYear() -> None:
    newYearsEve = datetime(2026, 12, 31, 12, 0, tzinfo=UTC)
    record = item("Салют", "2027-01-01T00:30:00+03:00", kind="reminder", all_day=False)
    reply = render(turn("done", created(record)), MSK, newYearsEve)
    assert "Завтра, пт 1 января 2027, 00:30" in str(reply["text"])


# Примечание: запись не по контракту (статус cancelled) в списке пропускается, остальные — на месте
def test_invalidItemSkipped() -> None:
    cancelled = BREAD | {"status": "cancelled", "id": str(uuid.uuid4())}
    reply = render(turn("answer", listed(*TODAY, EVENT, cancelled)))
    items = reply["items"]
    assert isinstance(items, list)
    assert [i["title"] for i in items] == ["Созвон с командой"]


# Примечание: busy — «ещё обрабатываю» со state=error
def test_busy() -> None:
    reply = TemplateReplyRenderer(loadTemplates(), FixedClock(NOW)).busy(TRACE)
    assert reply.state is ReplyState.ERROR
    assert reply.text.startswith("Ещё обрабатываю")


# Примечание: даты: год только у чужого года, неделя через границу месяцев
def test_dateFormatter() -> None:
    dates = DateFormatter(loadTemplates().dates, date(2026, 10, 21))
    assert dates.day(date(2026, 10, 23)) == "пт 23 октября"
    assert dates.day(date(2027, 1, 1)) == "пт 1 января 2027"
    assert dates.relativeDay(date(2026, 10, 20)) == "Вчера, вт 20 октября"
    assert dates.weekRange(date(2026, 10, 26), date(2026, 11, 1)) == "26 октября – 1 ноября"
    assert dates.weekRange(date(2026, 10, 19), date(2026, 10, 25)) == "19–25 октября"


# Примечание: экранирование: разметка внутри, служебные символы в начале строки, переносы
@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("Купить *хлеб*", r"Купить \*хлеб\*"),
        ("# план", r"\# план"),
        ("1. пункт", r"1\. пункт"),
        ("- минус", r"\- минус"),
        ("строка\nвторая", "строка вторая"),
        ("a_b~c`d", r"a\_b\~c\`d"),
    ],
)
def test_escapeMarkdown(raw: str, escaped: str) -> None:
    assert escapeMarkdown(raw) == escaped


# Примечание: шаблон без текста ошибки или с лишним ключом — ошибка при старте
def test_templatesValidated() -> None:
    raw = tomllib.loads(
        resources.files("agent.presentation").joinpath("templates", "ru.toml").read_text("utf-8")
    )
    errors = dict(raw["errors"])
    errors.pop("past_time")
    with pytest.raises(ValidationError, match="past_time"):
        ReplyTemplates.model_validate({**raw, "errors": errors})
    with pytest.raises(ValidationError):
        ReplyTemplates.model_validate({**raw, "buttons": {**raw["buttons"], "typo": "x"}})


# Примечание: «Распознал: …» — в markdown экранирован, в plain — как есть, одной строкой
def test_heardLine() -> None:
    renderer = TemplateReplyRenderer(loadTemplates(), FixedClock(NOW))
    user = UserContext(userId=17, maxUserId=1, maxChatId=2, tz=MSK)
    confirm = renderer.render(
        turn("done", created(REMINDER)), user, TRACE, heard="Напомни *срочно*\nИвану"
    )
    assert confirm.text.startswith("Распознал: «Напомни \\*срочно\\* Ивану»\nНапоминание создано")
    plain = renderer.render(turn("answer", say="Привет"), user, TRACE, heard="Привет *там*")
    assert plain.text == "Распознал: «Привет *там*»\nПривет"
    failed = renderer.render(turn("error", errorCode="past_time"), user, TRACE, heard="вчера")
    assert failed.text.startswith("Распознал: «вчера»\nЭто время уже прошло")


# Примечание: длинная фраза + длинный список — всё равно ≤ 4000 символов
def test_heardKeepsLimit() -> None:
    renderer = TemplateReplyRenderer(loadTemplates(), FixedClock(NOW))
    user = UserContext(userId=17, maxUserId=1, maxChatId=2, tz=MSK)
    reply = renderer.render(turn("answer", say="я" * 3990), user, TRACE, heard="ы" * 500)
    assert len(reply.text) <= REPLY_TEXT_MAX_LENGTH


# Примечание: у каждого Notice — свой текст из шаблонов, ответ без разметки
@pytest.mark.parametrize("notice", list(Notice))
def test_notices(notice: Notice) -> None:
    reply = TemplateReplyRenderer(loadTemplates(), FixedClock(NOW)).notice(notice, TRACE)
    assert reply.text
    expected = ReplyState.ERROR if notice is Notice.UNKNOWN_ACTION else ReplyState.ANSWER
    assert (reply.state, reply.format.value) == (expected, "plain")


# Примечание: напоминание в форме D — starts_at = null, время в remind_at: подтверждение и
# список показывают remind_at (а не «без срока»); период — датами J3 [from, to)
def test_reminderTimeFromRemindAt() -> None:
    dShaped = item(
        "Позвонить маме",
        None,
        kind="reminder",
        all_day=False,
        remind_at="2026-10-21T19:00:00+03:00",
    )
    confirm = render(turn("done", created(dShaped)))
    assert str(confirm["text"]).split("\n")[2] == "Сегодня, ср 21 октября, 19:00"
    listedReply = render(turn("answer", listed("2026-10-21", "2026-10-22", dShaped, EVENT)))
    lines = str(listedReply["text"]).split("\n")
    assert lines[0] == "Сегодня, ср 21 октября:"
    assert lines[1:] == ["10:00–11:00 Созвон с командой", "19:00 Позвонить маме"]
    week = render(turn("answer", listed("2026-10-19", "2026-10-26", dShaped)))
    assert str(week["text"]).startswith("Неделя, 19–25 октября:")
