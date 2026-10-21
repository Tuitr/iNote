import uuid
from datetime import UTC, datetime
from typing import Final

import pytest
import structlog

from agent.application.handle_message import HandleMessage, TurnDeps
from agent.application.ports import DialogContext
from agent.presentation.basic_renderer import BasicReplyRenderer
from common.errors import AppError, ErrorCode
from common.trace import traceContext
from contracts.agent_side import Clarify, ItemBrief, Turn, TurnInput, TurnResult
from contracts.reply import MessageRequest, Reply, ReplyState
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
)

TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
MAX_USER: Final = 100200300
USER_ID: Final = MAX_USER + 1
# 21.10.2026 18:30 UTC = 21:30 в Москве, 23:30 в Екатеринбурге
NOW: Final = datetime(2026, 10, 21, 18, 30, tzinfo=UTC)
REQUEST: Final = MessageRequest(
    maxUserId=MAX_USER, maxChatId=500600700, text="напомни завтра в 10 позвонить Ивану", name="Иван"
)


# Примечание: стенд из фейков; возвращает use case и все фейки для проверок
class Rig:
    # Примечание: brain задаёт сценарий хода
    def __init__(
        self,
        brain: ScenarioBrain,
        *,
        resolver: FakeUserResolver | None = None,
        store: InMemoryContextStore | None = None,
        turnTimeoutS: float = 5.0,
        lockWaitS: float = 0.05,
    ) -> None:
        self.brain = brain
        self.resolver = resolver or FakeUserResolver()
        self.lock = InMemoryUserLock()
        self.store = store or InMemoryContextStore()
        self.tools = RecordingToolFactory()
        self.handler = HandleMessage(
            TurnDeps(
                resolver=self.resolver,
                lock=self.lock,
                contextStore=self.store,
                clock=FixedClock(NOW),
                brain=brain,
                tools=self.tools,
                renderer=BasicReplyRenderer(),
            ),
            turnTimeoutS=turnTimeoutS,
            lockWaitS=lockWaitS,
        )

    # Примечание: ход внутри trace-контекста, как в запросе через middleware
    async def run(self, request: MessageRequest = REQUEST) -> Reply:
        with traceContext(TRACE):
            return await self.handler(request)


# Примечание: сценарий «модель создала запись»
def done(turn: TurnInput) -> TurnResult:
    return TurnResult(
        outcome="done",
        actions=[createdAction()],
        focus=[uuid.UUID(ITEM_ID)],
        usage=USAGE,
    )


# Примечание: сценарий со свободным ответом
def answer(turn: TurnInput) -> TurnResult:
    return TurnResult(outcome="answer", say="Пока понимаю только списки", usage=USAGE)


# Примечание: успешный ход: Reply done, лок снят, caller с внутренним user_id и trace_id
async def test_successfulTurn() -> None:
    rig = Rig(ScenarioBrain(done))
    reply = await rig.run()
    assert reply.state is ReplyState.DONE
    assert reply.traceId == TRACE
    assert rig.lock.held == {}
    assert rig.lock.released == 1
    [caller] = rig.tools.callers
    assert (caller.userId, caller.tz, caller.traceId) == (USER_ID, "Europe/Moscow", TRACE)
    assert rig.resolver.calls == [(MAX_USER, 500600700, "Иван")]


# Примечание: TurnInput: «сейчас» в поясе пользователя и никакого user_id (правило 1)
@pytest.mark.parametrize(("tz", "localHour"), [("Europe/Moscow", 21), ("Asia/Yekaterinburg", 23)])
async def test_turnInputInUserTzWithoutUserId(tz: str, localHour: int) -> None:
    rig = Rig(ScenarioBrain(answer), resolver=FakeUserResolver(tz=tz))
    await rig.run()
    [turn] = rig.brain.turns
    assert turn.now.hour == localHour
    assert turn.tz == tz
    assert turn.settings.tz == tz
    wire = turn.toJson()
    assert str(USER_ID) not in wire
    assert "user_id" not in wire


# Примечание: история, фокус и pending сохраняются после хода; фокус — как ItemBrief из data
async def test_contextSaved() -> None:
    rig = Rig(ScenarioBrain(done))
    await rig.run()
    context = rig.store.contexts[USER_ID]
    assert [t.role for t in context.history] == ["user", "assistant"]
    assert context.history[0].text == REQUEST.text
    assert [b.title for b in context.focus] == ["Позвонить Ивану"]
    assert context.pending is None


# Примечание: история обрезается до 10 реплик; прошлый фокус переживает ход, если Brain его держит
async def test_historyLimitAndFocusCarryOver() -> None:
    old = Turn(role="user", text="старое", at=NOW)
    brief = ItemBrief.fromWire(ITEM)
    store = InMemoryContextStore()
    store.contexts[USER_ID] = DialogContext(history=(old,) * 10, focus=(brief,))

    # Примечание: Brain отвечает и оставляет запись в фокусе
    def keepFocus(turn: TurnInput) -> TurnResult:
        assert len(turn.history) == 10
        assert turn.focus == [brief]
        return TurnResult(outcome="answer", say="ок", focus=[brief.id], usage=USAGE)

    rig = Rig(ScenarioBrain(keepFocus), store=store)
    await rig.run()
    context = store.contexts[USER_ID]
    assert len(context.history) == 10
    assert context.history[-1].role == "assistant"
    assert context.focus == (brief,)


# Примечание: уточнение — pending сохраняется как есть, без разбора
async def test_pendingSavedOpaque() -> None:
    pending: dict[str, object] = {"tool": "create_event", "anything": [1, {"x": None}]}

    # Примечание: Brain просит время
    def clarify(turn: TurnInput) -> TurnResult:
        return TurnResult(
            outcome="clarify",
            say="На какое время?",
            clarify=Clarify(missing=["starts_at"], options=["10:00"], pending=pending),
            usage=USAGE,
        )

    rig = Rig(ScenarioBrain(clarify))
    reply = await rig.run()
    assert reply.state is ReplyState.CLARIFY
    assert rig.store.contexts[USER_ID].pending == pending


# Примечание: answer — текст модели; error Brain — Reply error с кодом цепочки
async def test_answerAndError() -> None:
    reply = await Rig(ScenarioBrain(answer)).run()
    assert reply.state is ReplyState.ANSWER
    assert reply.text == "Пока понимаю только списки"

    # Примечание: LLM не ответила вовремя
    def llmError(turn: TurnInput) -> TurnResult:
        return TurnResult(outcome="error", errorCode="llm_timeout", usage=USAGE)

    reply = await Rig(ScenarioBrain(llmError)).run()
    assert reply.state is ReplyState.ERROR
    assert "ab12f0" in reply.text


# Примечание: правило 3 — «готово» без успешного изменяющего вызова превращается в llm_no_action
@pytest.mark.parametrize("withFailedAction", [False, True])
async def test_doneWithoutMutationIsLlmNoAction(withFailedAction: bool) -> None:
    failed = createdAction().model_copy(
        update={
            "result": createdAction().result.model_copy(
                update={"ok": False, "error": {"code": "past_time", "message": "x"}}
            )
        }
    )

    # Примечание: модель «сделала», но изменяющего вызова не было или он упал
    def fakeDone(turn: TurnInput) -> TurnResult:
        return TurnResult(
            outcome="done",
            say="Записал!",
            actions=[failed] if withFailedAction else [],
            usage=USAGE,
        )

    rig = Rig(ScenarioBrain(fakeDone))
    with structlog.testing.capture_logs() as logs:
        reply = await rig.run()
    assert reply.state is ReplyState.ERROR
    assert "Записал" not in reply.text
    assert any(e["event"] == "llm_no_action" for e in logs)
    assert any(e.get("error_code") == "llm_no_action" for e in logs)


# Примечание: лок занят другим ходом — «ещё обрабатываю», Brain не вызывался
async def test_busyLock() -> None:
    rig = Rig(ScenarioBrain(done))
    rig.lock.held[USER_ID] = "чужой"
    reply = await rig.run()
    assert reply.text.startswith("Ещё обрабатываю")
    assert rig.brain.turns == []
    assert rig.lock.held == {USER_ID: "чужой"}


# Примечание: ход дольше бюджета — upstream_timeout, лок снят
async def test_turnTimeoutReleasesLock() -> None:
    rig = Rig(ScenarioBrain(done, delayS=1.0), turnTimeoutS=0.05)
    with structlog.testing.capture_logs() as logs:
        reply = await rig.run()
    assert reply.state is ReplyState.ERROR
    assert rig.lock.held == {}
    assert any(e["event"] == "turn_timeout" for e in logs)


# Примечание: исключение в Brain — Reply internal, лок снят, исключение не уходит в HTTP
async def test_brainCrashReleasesLock() -> None:
    rig = Rig(ScenarioBrain(done, error=RuntimeError("boom")))
    reply = await rig.run()
    assert reply.state is ReplyState.ERROR
    assert rig.lock.held == {}
    assert rig.lock.released == 1


# Примечание: J8 недоступен — ответ с ошибкой, лок даже не берём
async def test_resolverUnavailable() -> None:
    resolver = FakeUserResolver(error=AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "J8 down"))
    rig = Rig(ScenarioBrain(done), resolver=resolver)
    reply = await rig.run()
    assert reply.state is ReplyState.ERROR
    assert rig.lock.acquired == 0


# Примечание: сбой записи контекста не ломает ответ пользователю
async def test_contextSaveFailureStillReplies() -> None:
    rig = Rig(ScenarioBrain(done), store=InMemoryContextStore(failSave=True))
    with structlog.testing.capture_logs() as logs:
        reply = await rig.run()
    assert reply.state is ReplyState.DONE
    assert any(e["event"] == "context_save_failed" for e in logs)


# Примечание: в логах хода нет текста пользователя (правило 10)
async def test_logsHaveNoUserText() -> None:
    rig = Rig(ScenarioBrain(done))
    with structlog.testing.capture_logs() as logs:
        await rig.run()
    assert "позвонить" not in str(logs).lower()
