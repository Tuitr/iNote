"""HandleAction: выбор варианта → resume под локом, «Отмена», устаревший вопрос, заглушка."""

from datetime import UTC, datetime
from typing import Final

from agent.application.handle_action import HandleAction
from agent.application.ports import DialogContext
from agent.application.turn_pipeline import TurnDeps, TurnPipeline
from agent.infrastructure.stub_replies import ExampleReplySource, loadReplyExample
from agent.presentation.reply_renderer import TemplateReplyRenderer
from agent.presentation.templates import loadTemplates
from common.trace import traceContext
from contracts.agent_side import Clarify, TurnInput, TurnResult
from contracts.reply import ActionRequest, Reply, ReplyState
from services.agent.tests.conftest import EXAMPLES
from services.agent.tests.fakes import (
    USAGE,
    FakeUserResolver,
    FixedClock,
    InMemoryContextStore,
    InMemoryUserLock,
    RecordingToolFactory,
    ScenarioBrain,
    createdAction,
)

TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
NOW: Final = datetime(2026, 10, 21, 18, 30, tzinfo=UTC)
MAX_USER: Final = 100200300
USER_ID: Final = MAX_USER + 1
PENDING: Final[dict[str, object]] = {"kind": "time", "anything": [1, 2]}


# Примечание: стенд HandleAction на фейках; context — что лежит в Redis до нажатия
class Rig:
    # Примечание: brain — что ответит resume
    def __init__(self, brain: ScenarioBrain, context: DialogContext | None = None) -> None:
        self.brain = brain
        self.lock = InMemoryUserLock()
        self.store = InMemoryContextStore()
        if context is not None:
            self.store.contexts[USER_ID] = context
        self.tools = RecordingToolFactory()
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
        fallback = ExampleReplySource(loadReplyExample(EXAMPLES / "reply" / "list.json"))
        self.handler = HandleAction(pipeline, fallback)

    # Примечание: нажатие кнопки в trace-контексте
    async def press(self, action: str, args: dict[str, str | int | bool] | None = None) -> Reply:
        request = ActionRequest(maxUserId=MAX_USER, maxChatId=5, action=action, args=args or {})
        with traceContext(TRACE):
            return await self.handler(request)


# Примечание: resume создаёт запись
def created(turn: TurnInput) -> TurnResult:
    return TurnResult(outcome="done", actions=[createdAction()], usage=USAGE)


# Примечание: живой вопрос с вариантами
def asked() -> DialogContext:
    return DialogContext(pending=PENDING, options=("сегодня 13:00", "сегодня 19:00"))


# Примечание: выбор варианта → resume с номером и подписью, pending удалён, лок взят и снят
async def test_pickOption() -> None:
    rig = Rig(ScenarioBrain(created), asked())
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
        rig = Rig(ScenarioBrain(created), context)
        reply = await rig.press("pick_option", {"choice": choice})
        assert reply.text == loadTemplates().notices.pendingExpired
        assert rig.brain.turns == []


# Примечание: «Отмена» — pending и варианты стёрты, история цела; без pending — тот же ответ
async def test_cancel() -> None:
    rig = Rig(ScenarioBrain(created), asked())
    reply = await rig.press("cancel")
    assert reply.text == loadTemplates().notices.cancelled
    context = rig.store.contexts[USER_ID]
    assert (context.pending, context.options) == (None, ())
    assert (await Rig(ScenarioBrain(created)).press("cancel")).text == reply.text


# Примечание: действия шага 4 пока отвечают фиксированным Reply с trace текущей цепочки
async def test_otherActionsUseStub() -> None:
    reply = await Rig(ScenarioBrain(created)).press("list_period", {"period": "week"})
    assert reply.traceId == TRACE
    assert reply.state is ReplyState.ANSWER
