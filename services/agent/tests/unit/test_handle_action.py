"""HandleAction: каталог /v1/actions без LLM на фейковом ToolExecutor и ScenarioBrain."""

from datetime import UTC, datetime
from typing import Final

import pytest

from agent.application.handle_action import HandleAction
from agent.application.ports import DialogContext, SettingsChange
from agent.application.turn_pipeline import TurnDeps, TurnPipeline
from agent.presentation.reply_renderer import TemplateReplyRenderer
from agent.presentation.templates import loadTemplates
from common.errors import AppError, ErrorCode
from common.trace import traceContext
from contracts.agent_side import Clarify, ItemBrief, ToolResult, TurnInput, TurnResult
from contracts.reply import ActionButton, ActionRequest, Reply, ReplyState
from services.agent.tests.fakes import (
    ITEM,
    ITEM_ID,
    USAGE,
    FakeUserResolver,
    FixedClock,
    InMemoryContextStore,
    InMemorySettingsWriter,
    InMemoryUserLock,
    RecordingDialogLog,
    RecordingToolFactory,
    ScenarioBrain,
    createdAction,
    mutation,
    toolError,
)

TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
NOW: Final = datetime(2026, 10, 21, 18, 30, tzinfo=UTC)
MAX_USER: Final = 100200300
USER_ID: Final = MAX_USER + 1
PENDING: Final[dict[str, object]] = {"kind": "time", "anything": [1, 2]}


# Примечание: стенд HandleAction на фейках; context — что лежит в Redis до нажатия
class Rig:
    # Примечание: brain — что ответит resume; responses — ответы инструментов по имени
    def __init__(
        self,
        brain: ScenarioBrain | None = None,
        context: DialogContext | None = None,
        responses: dict[str, ToolResult] | None = None,
    ) -> None:
        brain = brain or ScenarioBrain(created)
        self.brain = brain
        self.lock = InMemoryUserLock()
        self.store = InMemoryContextStore()
        if context is not None:
            self.store.contexts[USER_ID] = context
        self.dialog = RecordingDialogLog()
        self.tools = RecordingToolFactory(responses)
        self.resolver = FakeUserResolver()
        self.settings = InMemorySettingsWriter(self.resolver)
        pipeline = TurnPipeline(
            TurnDeps(
                resolver=self.resolver,
                lock=self.lock,
                contextStore=self.store,
                clock=FixedClock(NOW),
                brain=brain,
                tools=self.tools,
                renderer=TemplateReplyRenderer(loadTemplates(), FixedClock(NOW)),
                dialogLog=self.dialog,
            ),
            lockWaitS=0.05,
        )
        self.handler = HandleAction(pipeline, self.settings)

    # Примечание: нажатие кнопки в trace-контексте
    async def press(
        self,
        action: str,
        args: dict[str, str | int | bool] | None = None,
        *,
        label: str | None = None,
    ) -> Reply:
        request = ActionRequest(
            maxUserId=MAX_USER, maxChatId=5, action=action, args=args or {}, label=label
        )
        with traceContext(TRACE):
            return await self.handler(request)


# Примечание: resume создаёт запись (Brain по умолчанию — в действиях без LLM не вызывается)
def created(turn: TurnInput) -> TurnResult:
    return TurnResult(outcome="done", actions=[createdAction()], usage=USAGE)


# Примечание: живой вопрос с вариантами
def asked() -> DialogContext:
    return DialogContext(pending=PENDING, options=("сегодня 13:00", "сегодня 19:00"))


# Примечание: выбор варианта → resume с номером и подписью, pending удалён, лок взят и снят
async def test_pickOption() -> None:
    rig = Rig(context=asked())
    reply = await rig.press("pick_option", {"choice": 1})
    assert reply.state is ReplyState.DONE
    assert rig.brain.choices == ["1"]
    [turn] = rig.brain.turns
    assert (turn.text, turn.pending) == ("сегодня 19:00", PENDING)
    context = rig.store.contexts[USER_ID]
    assert (context.pending, context.options) == (None, ())
    assert [t.text for t in context.history][:1] == ["сегодня 19:00"]
    assert (rig.lock.acquired, rig.lock.released, rig.lock.held) == (1, 1, {})
    [caller] = rig.tools.callers
    assert caller.userId == USER_ID


# Примечание: resume может снова спросить — новый pending и варианты сохраняются
async def test_pickOptionAsksAgain() -> None:
    again: dict[str, object] = {"kind": "slot"}

    # Примечание: Brain уточняет слот
    def clarify(turn: TurnInput) -> TurnResult:
        options = Clarify(missing=["starts_at"], options=["16:00–17:00"], pending=again)
        return TurnResult(outcome="clarify", say="Занято", clarify=options, usage=USAGE)

    rig = Rig(ScenarioBrain(clarify), asked())
    reply = await rig.press("pick_option", {"choice": 0})
    assert reply.state is ReplyState.CLARIFY
    context = rig.store.contexts[USER_ID]
    assert (context.pending, context.options) == (again, ("16:00–17:00",))


# Примечание: pending нет (истёк 10 мин) или номер вне списка — «Вопрос устарел», Brain не зовём
async def test_pendingExpired() -> None:
    for context, choice in ((None, 0), (asked(), 5), (asked(), True)):
        rig = Rig(context=context)
        reply = await rig.press("pick_option", {"choice": choice})
        assert reply.text == loadTemplates().notices.pendingExpired
        assert rig.brain.turns == []


# Примечание: «Отмена» — pending и варианты стёрты, история цела; без pending — тот же ответ
async def test_cancel() -> None:
    rig = Rig(context=asked())
    reply = await rig.press("cancel")
    assert reply.text == loadTemplates().notices.cancelled
    context = rig.store.contexts[USER_ID]
    assert (context.pending, context.options) == (None, ())
    assert (await Rig().press("cancel")).text == reply.text


TEMPLATES: Final = loadTemplates()
BRIEF: Final = ItemBrief.fromWire(ITEM)


# Примечание: кнопки ответа построчно: (текст, действие, args) или (текст, start_param)
def buttons(reply: Reply) -> list[list[tuple[object, ...]]]:
    return [
        [
            (b.text, b.action, b.args) if isinstance(b, ActionButton) else (b.text, b.startParam)
            for b in row
        ]
        for row in reply.buttons
    ]


# Примечание: «Отменить» → delete_item только с id: user_id из args не доходит до инструмента,
# caller — внутренний пользователь; запись уходит из фокуса, кнопок у удалённого нет
async def test_undo() -> None:
    rig = Rig(context=DialogContext(focus=(BRIEF,)), responses={"delete_item": mutation("deleted")})
    reply = await rig.press("undo", {"item_id": ITEM_ID, "user_id": 1, "caller": "x"})
    assert rig.tools.calls == [("delete_item", {"id": ITEM_ID})]
    [caller] = rig.tools.callers
    assert (caller.userId, caller.traceId) == (USER_ID, TRACE)
    assert reply.state is ReplyState.DONE
    assert reply.text.splitlines()[:2] == [TEMPLATES.changed.deleted, "Позвонить Ивану"]
    assert reply.buttons == []
    assert rig.store.contexts[USER_ID].focus == ()
    assert (rig.lock.acquired, rig.lock.released) == (1, 1)
    assert rig.brain.turns == []


# Примечание: второе нажатие «Отменить» (запись уже удалена) — «Уже отменено», не ошибка
async def test_undoTwice() -> None:
    rig = Rig(responses={"delete_item": toolError("not_found")})
    reply = await rig.press("undo", {"item_id": ITEM_ID})
    assert (reply.text, reply.state) == (TEMPLATES.notices.alreadyUndone, ReplyState.ANSWER)


# Примечание: другая ошибка инструмента — текст ошибки с кодом цепочки
async def test_undoUpstreamFailure() -> None:
    rig = Rig(responses={"delete_item": toolError("upstream_timeout")})
    reply = await rig.press("undo", {"item_id": ITEM_ID})
    assert reply.state is ReplyState.ERROR
    assert reply.text == f"{TEMPLATES.errors['upstream_timeout']}\nКод: ab12f0"


# Примечание: перенос со starts_at → update_item и подтверждение из данных; «Отменить» не даём
async def test_rescheduleWithTime() -> None:
    moved = {**ITEM, "starts_at": "2026-10-22T15:00:00+03:00"}
    rig = Rig(responses={"update_item": mutation("updated", moved)})
    reply = await rig.press(
        "reschedule", {"item_id": ITEM_ID, "starts_at": "2026-10-22T15:00:00+03:00"}
    )
    assert rig.tools.calls == [
        ("update_item", {"id": ITEM_ID, "starts_at": "2026-10-22T15:00:00+03:00"})
    ]
    assert reply.state is ReplyState.DONE
    assert reply.text.splitlines() == [
        TEMPLATES.changed.updated,
        "Позвонить Ивану",
        "Завтра, чт 22 октября, 15:00",
    ]
    assert [b[1] for b in buttons(reply)[0]] == ["reschedule"]
    assert [str(b.id) for b in rig.store.contexts[USER_ID].focus] == [ITEM_ID]


# Примечание: без starts_at — вопрос с вариантами и «Отмена»; запись в фокусе — на завтра 10:00,
# поэтому «завтра в это же время» не предлагается;
# прежний вопрос снят, фокус — на этой записи (ответ текстом разберёт Brain)
async def test_rescheduleAsksWhen() -> None:
    other = ItemBrief.fromWire({**ITEM, "id": "2b8d4e61-0c3f-4a7b-8e95-1f6a2d3c4b57"})
    context = DialogContext(focus=(other, BRIEF), pending=PENDING, options=("a",))
    rig = Rig(context=context)
    reply = await rig.press("reschedule", {"item_id": ITEM_ID})
    assert (reply.text, reply.state) == (TEMPLATES.reschedule.question, ReplyState.CLARIFY)
    assert buttons(reply) == [
        [
            (
                "Через час",
                "reschedule",
                {
                    "item_id": ITEM_ID,
                    "starts_at": "2026-10-21T22:30:00+03:00",
                    "kind": "reminder",
                },
            )
        ],
        [
            (
                "Завтра в 9:00",
                "reschedule",
                {
                    "item_id": ITEM_ID,
                    "starts_at": "2026-10-22T09:00:00+03:00",
                    "kind": "reminder",
                },
            )
        ],
        [("Отмена", "cancel", {}), ("В календаре", f"item_{ITEM_ID}")],
    ]
    saved = rig.store.contexts[USER_ID]
    assert (saved.focus, saved.pending, saved.options) == ((BRIEF,), None, ())
    assert rig.tools.calls == []


# Примечание: /week и кнопки периода → list_items с [from, to) в поясе пользователя
async def test_listPeriod() -> None:
    listed = ToolResult(
        ok=True,
        summary="1",
        data={"items": [ITEM], "range": {"from": "x", "to": "y"}, "truncated": False},
    )
    rig = Rig(responses={"list_items": listed})
    reply = await rig.press("list_period", {"period": "week"})
    assert rig.tools.calls == [("list_items", {"from": "2026-10-19", "to": "2026-10-26"})]
    assert reply.text.splitlines()[0] == "Неделя, 19–25 октября:"
    assert [str(b.id) for b in rig.store.contexts[USER_ID].focus] == [ITEM_ID]


# Примечание: «Начать» и /help — тексты агента, кнопки периода, «Мой день» (today) и «История»
# (dialog); инструменты не нужны
async def test_startAndHelp() -> None:
    rig = Rig()
    start, help_ = await rig.press("start"), await rig.press("help")
    assert (start.text, help_.text) == (TEMPLATES.welcome.start, TEMPLATES.welcome.help)
    assert buttons(start)[-1] == [
        (TEMPLATES.welcome.openToday, "today"),
        (TEMPLATES.welcome.openHistory, "dialog"),
    ]
    assert [b[2] for b in buttons(help_)[0]] == [
        {"period": "today"},
        {"period": "tomorrow"},
        {"period": "week"},
    ]
    assert rig.tools.calls == []
    assert len(rig.tools.callers) == 2


# Примечание: незнакомое действие или args не по каталогу — «Не понял кнопку», без лока и вызовов
async def test_unknownAction() -> None:
    cases: list[tuple[str, dict[str, str | int | bool]]] = [
        ("snooze", {"item_id": ITEM_ID}),
        ("undo", {}),
        ("undo", {"item_id": "../../etc"}),
        ("reschedule", {"item_id": ITEM_ID, "starts_at": "2026-10-22T15:00"}),
        ("list_period", {"period": "month"}),
    ]
    for action, args in cases:
        rig = Rig()
        reply = await rig.press(action, args)
        assert (reply.text, reply.state) == (TEMPLATES.notices.unknownAction, ReplyState.ERROR)
        assert reply.traceId == TRACE
        assert (rig.lock.acquired, rig.tools.calls) == (0, [])


# Примечание: в историю (input_type=text, как у D): подпись кнопки от бота, без неё — /действие,
# выбор варианта — подпись варианта;
# незнакомая кнопка (без хода) — без записи
async def test_dialogEntries() -> None:
    rig = Rig(context=asked(), responses={"delete_item": mutation("deleted")})
    await rig.press("pick_option", {"choice": 1})
    await rig.press("undo", {"item_id": ITEM_ID})
    await rig.press("list_period", {"period": "today"}, label="Сегодня")
    await rig.press("teleport")
    said = [(e.said.text, e.said.inputType) for e in rig.dialog.entries]
    assert said == [("сегодня 19:00", "text"), ("/undo", "text"), ("Сегодня", "text")]
    assert {e.traceId for e in rig.dialog.entries} == {TRACE}


# Примечание: перенос по кнопке двигает поле времени по виду записи: у напоминания — remind_at,
# у встречи — starts_at; вид из кнопки, без него — из фокуса; чужой вид — «Не понял кнопку»
async def test_rescheduleMovesFieldByKind() -> None:
    when = "2026-10-22T15:00:00+03:00"
    rig = Rig(responses={"update_item": mutation("updated")})
    await rig.press("reschedule", {"item_id": ITEM_ID, "starts_at": when, "kind": "event"})
    await rig.press("reschedule", {"item_id": ITEM_ID, "starts_at": when, "kind": "reminder"})
    focused = Rig(
        context=DialogContext(focus=(BRIEF,)), responses={"update_item": mutation("updated")}
    )
    await focused.press("reschedule", {"item_id": ITEM_ID, "starts_at": when})
    assert [c[1] for c in rig.tools.calls + focused.tools.calls] == [
        {"id": ITEM_ID, "starts_at": when},
        {"id": ITEM_ID, "remind_at": when},
        {"id": ITEM_ID, "remind_at": when},
    ]
    bad = await Rig().press("reschedule", {"item_id": ITEM_ID, "starts_at": when, "kind": "x"})
    assert bad.text == TEMPLATES.notices.unknownAction


DONE_ITEM: Final = {**ITEM, "status": "done"}


# Примечание: «Выполнено» → complete_item только с id (user_id из args не доходит); ответ из
# данных без «Отменить» и «Изменить время»; повтор (в фокусе уже done) — «Уже отмечено» без
# вызова MCP; удалённая — текст not_found
async def test_complete() -> None:
    rig = Rig(responses={"complete_item": mutation("completed", DONE_ITEM)})
    reply = await rig.press("complete", {"item_id": ITEM_ID, "kind": "reminder", "user_id": 1})
    assert rig.tools.calls == [("complete_item", {"id": ITEM_ID})]
    assert reply.text.splitlines()[:2] == [TEMPLATES.changed.completed, "Позвонить Ивану"]
    assert [[b[0] for b in row] for row in buttons(reply)] == [["В календаре"]]
    assert [str(b.id) for b in rig.store.contexts[USER_ID].focus] == [ITEM_ID]
    again = Rig(context=DialogContext(focus=(ItemBrief.fromWire(DONE_ITEM),)))
    repeated = await again.press("complete", {"item_id": ITEM_ID})
    assert (repeated.text, repeated.state) == (
        TEMPLATES.notices.alreadyCompleted,
        ReplyState.ANSWER,
    )
    assert again.tools.calls == []
    gone = await Rig(responses={"complete_item": toolError("not_found")}).press(
        "complete", {"item_id": ITEM_ID}
    )
    assert gone.text.startswith(TEMPLATES.errors["not_found"])


# Примечание: «Отложить на 1 ч» — remind_at = «сейчас» + 60 мин в поясе пользователя;
# «Завтра» — время напоминания из фокуса завтра; ответ «Напомню …»
async def test_snooze() -> None:
    later = {**ITEM, "remind_at": "2026-10-21T22:30:00+03:00", "starts_at": None}
    rig = Rig(responses={"update_item": mutation("updated", later)})
    reply = await rig.press("snooze", {"item_id": ITEM_ID, "kind": "reminder", "minutes": 60})
    assert rig.tools.calls == [
        ("update_item", {"id": ITEM_ID, "remind_at": "2026-10-21T22:30:00+03:00"})
    ]
    assert reply.text == "Напомню: Сегодня, ср 21 октября, 22:30\nПозвонить Ивану"
    assert reply.state is ReplyState.DONE
    focused = ItemBrief.fromWire(
        {**ITEM, "starts_at": None, "remind_at": "2026-10-21T21:00:00+03:00"}
    )
    tomorrow = Rig(
        context=DialogContext(focus=(focused,)),
        responses={"update_item": mutation("updated", later)},
    )
    await tomorrow.press("snooze", {"item_id": ITEM_ID, "until": "tomorrow"})
    assert tomorrow.tools.calls == [
        ("update_item", {"id": ITEM_ID, "remind_at": "2026-10-22T21:00:00+03:00"})
    ]


# Примечание: snooze с плохими args — «Не понял кнопку», без вызовов
@pytest.mark.parametrize(
    "args",
    [
        {"item_id": ITEM_ID},
        {"item_id": ITEM_ID, "minutes": 0},
        {"item_id": ITEM_ID, "minutes": 2000},
        {"item_id": ITEM_ID, "minutes": True},
        {"item_id": ITEM_ID, "until": "monday"},
        {"item_id": ITEM_ID, "minutes": 60, "until": "tomorrow"},
        {"item_id": ITEM_ID, "minutes": 60, "kind": "memo"},
    ],
)
async def test_snoozeBadArgs(args: dict[str, str | int | bool]) -> None:
    rig = Rig()
    reply = await rig.press("snooze", args)
    assert reply.text == TEMPLATES.notices.unknownAction
    assert rig.tools.calls == []


# Примечание: «Всё равно создать» — create_event c allow_conflict по данным кнопки, вопрос снят;
# без живого вопроса — «Вопрос устарел» и ничего не создаётся
async def test_confirmConflict() -> None:
    event = {**ITEM, "kind": "event", "title": "Встреча с Петром", "remind_at": None}
    args: dict[str, str | int | bool] = {
        "title": "Встреча с Петром",
        "starts_at": "2026-10-22T09:30:00+03:00",
        "ends_at": "2026-10-22T10:30:00+03:00",
    }
    rig = Rig(context=asked(), responses={"create_event": mutation("created", event)})
    reply = await rig.press("confirm_conflict", args)
    assert rig.tools.calls == [("create_event", {**args, "allow_conflict": True})]
    assert reply.text.splitlines()[0] == TEMPLATES.created.event
    saved = rig.store.contexts[USER_ID]
    assert (saved.pending, saved.options) == (None, ())
    stale = Rig(responses={"create_event": mutation("created", event)})
    expired = await stale.press("confirm_conflict", args)
    assert expired.text == TEMPLATES.notices.pendingExpired
    assert stale.tools.calls == []
    bad = await Rig(context=asked()).press("confirm_conflict", {"title": "x"})
    assert bad.text == TEMPLATES.notices.unknownAction


# Примечание: «Всё равно перенести» — update_item той же записи с allow_conflict, вопрос снят
async def test_confirmConflictMove() -> None:
    event = {**ITEM, "kind": "event", "title": "Встреча с Петром", "remind_at": None}
    args: dict[str, str | int | bool] = {
        "item_id": ITEM_ID,
        "starts_at": "2026-10-22T09:30:00+03:00",
    }
    rig = Rig(context=asked(), responses={"update_item": mutation("updated", event)})
    await rig.press("confirm_conflict", args)
    assert rig.tools.calls == [
        (
            "update_item",
            {"id": ITEM_ID, "starts_at": "2026-10-22T09:30:00+03:00", "allow_conflict": True},
        )
    ]
    assert rig.store.contexts[USER_ID].pending is None


# Примечание: /settings — пояс текстом; 12 поясов по 4 в ряд (≤ 7), текущий отмечен; голосовых
# ответов нет; инструменты не нужны
async def test_settingsScreen() -> None:
    rig = Rig()
    reply = await rig.press("settings")
    assert reply.text.split("\n")[1] == "Пояс: Москва (UTC+3)"
    assert "олосов" not in reply.text
    zones = buttons(reply)
    assert [len(row) for row in zones] == [4, 4, 4]
    flat = [b for row in zones for b in row]
    assert flat[1] == ("✓ Москва", "set_tz", {"tz": "Europe/Moscow"})
    assert flat[3] == ("Екатеринбург", "set_tz", {"tz": "Asia/Yekaterinburg"})
    assert rig.tools.calls == []


# Примечание: смена пояса → J8 под user_id из resolve (user_id из args игнорируется) →
# «Пояс: Камчатка (UTC+12)»; следующий «сегодня» — уже по дню Камчатки (там 22 октября)
async def test_setTzThenTodayInNewZone() -> None:
    rig = Rig(responses={"list_items": ToolResult(ok=True, summary="ok", data={"items": []})})
    before = await rig.press("list_period", {"period": "today"})
    reply = await rig.press("set_tz", {"tz": "Asia/Kamchatka", "user_id": 999})
    after = await rig.press("list_period", {"period": "today"})
    assert reply.text == "Пояс: Камчатка (UTC+12)"
    assert buttons(reply) == [[("Сегодня", "list_period", {"period": "today"})]]
    assert rig.settings.calls == [(USER_ID, SettingsChange(tz="Asia/Kamchatka"))]
    days = [args["from"] for name, args in rig.tools.calls if name == "list_items"]
    assert days == ["2026-10-21", "2026-10-22"]
    assert (before.state, after.state) == (ReplyState.ANSWER, ReplyState.ANSWER)


# Примечание: пояс не из белого списка, пустой и не строка; enabled не bool — «Не понял кнопку»,
# в J8 не ходим
@pytest.mark.parametrize(
    ("action", "args"),
    [
        ("set_tz", {"tz": "Europe/London"}),
        ("set_tz", {"tz": "Asia/Kolkata"}),
        ("set_tz", {}),
        ("set_tz", {"tz": 3}),
        # голосовых ответов в продукте нет — старое действие не по каталогу
        ("set_voice_replies", {"enabled": True}),
    ],
)
async def test_settingsRejectsBadArgs(action: str, args: dict[str, str | int | bool]) -> None:
    rig = Rig()
    reply = await rig.press(action, args)
    assert reply.text == TEMPLATES.notices.unknownAction
    assert rig.settings.calls == []


# Примечание: J8 недоступен — ответ-ошибка с кодом, пояс не поменялся
async def test_settingsWriterDown() -> None:
    rig = Rig()
    rig.settings.error = AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "down")
    reply = await rig.press("set_tz", {"tz": "Asia/Omsk"})
    assert reply.state is ReplyState.ERROR
    assert rig.resolver.tz == "Europe/Moscow"
