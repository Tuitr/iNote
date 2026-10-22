"""HandleAction: каталог /v1/actions без LLM на фейковом ToolExecutor и ScenarioBrain."""

from datetime import UTC, datetime
from typing import Final

from agent.application.handle_action import HandleAction
from agent.application.ports import DialogContext
from agent.application.turn_pipeline import TurnDeps, TurnPipeline
from agent.presentation.reply_renderer import TemplateReplyRenderer
from agent.presentation.templates import loadTemplates
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
    InMemoryUserLock,
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
        self.tools = RecordingToolFactory(responses)
        pipeline = TurnPipeline(
            TurnDeps(
                resolver=FakeUserResolver(),
                lock=self.lock,
                contextStore=self.store,
                clock=FixedClock(NOW),
                brain=brain,
                tools=self.tools,
                renderer=TemplateReplyRenderer(loadTemplates(), FixedClock(NOW)),
            ),
            lockWaitS=0.05,
        )
        self.handler = HandleAction(pipeline)

    # Примечание: нажатие кнопки в trace-контексте
    async def press(self, action: str, args: dict[str, str | int | bool] | None = None) -> Reply:
        request = ActionRequest(maxUserId=MAX_USER, maxChatId=5, action=action, args=args or {})
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
                {"item_id": ITEM_ID, "starts_at": "2026-10-21T22:30:00+03:00"},
            )
        ],
        [
            (
                "Завтра в 9:00",
                "reschedule",
                {"item_id": ITEM_ID, "starts_at": "2026-10-22T09:00:00+03:00"},
            )
        ],
        [("Отмена", "cancel", {})],
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
    assert rig.tools.calls == [
        ("list_items", {"from": "2026-10-19T00:00:00+03:00", "to": "2026-10-26T00:00:00+03:00"})
    ]
    assert reply.text.splitlines()[0] == "Неделя, 19–25 октября:"
    assert [str(b.id) for b in rig.store.contexts[USER_ID].focus] == [ITEM_ID]


# Примечание: «Начать» и /help — тексты агента, кнопки периода и «Мой день»; инструменты не нужны
async def test_startAndHelp() -> None:
    rig = Rig()
    start, help_ = await rig.press("start"), await rig.press("help")
    assert (start.text, help_.text) == (TEMPLATES.welcome.start, TEMPLATES.welcome.help)
    assert buttons(start)[-1] == [(TEMPLATES.welcome.openToday, "today")]
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
