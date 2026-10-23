"""HandleFocus (POST /v1/focus): запись уведомления — фокус под локом; pending и история целы."""

from datetime import UTC, datetime
from typing import Final

import pytest

from agent.application.handle_focus import HandleFocus
from agent.application.ports import DialogContext
from agent.application.turn_pipeline import TurnDeps
from agent.presentation.reply_renderer import TemplateReplyRenderer
from agent.presentation.templates import loadTemplates
from common.errors import AppError, ErrorCode
from contracts.agent_side import ItemBrief, TurnInput, TurnResult
from contracts.focus import FocusRequest
from services.agent.tests.fakes import (
    ITEM,
    FakeUserResolver,
    FixedClock,
    InMemoryContextStore,
    InMemoryUserLock,
    RecordingDialogLog,
    RecordingToolFactory,
    ScenarioBrain,
)

NOW: Final = datetime(2026, 10, 21, 18, 30, tzinfo=UTC)
USER: Final = 101  # FakeUserResolver: user_id = max_user_id + 1


# Примечание: Brain фокусу не нужен
def _neverCalled(turn: TurnInput) -> TurnResult:
    raise AssertionError(turn.text)


# Примечание: use case на фейках; lock и store — для проверок
def handler(lock: InMemoryUserLock, store: InMemoryContextStore) -> HandleFocus:
    deps = TurnDeps(
        resolver=FakeUserResolver(),
        lock=lock,
        contextStore=store,
        clock=FixedClock(NOW),
        brain=ScenarioBrain(_neverCalled),
        tools=RecordingToolFactory(),
        renderer=TemplateReplyRenderer(loadTemplates(), FixedClock(NOW)),
        dialogLog=RecordingDialogLog(),
    )
    return HandleFocus(deps, lockWaitS=0.05)


# Примечание: запрос бота про запись из примеров
def request() -> FocusRequest:
    return FocusRequest(maxUserId=100, maxChatId=200, item=ItemBrief.fromWire(ITEM))


# Примечание: фокус заменяется записью уведомления; недоделанный вопрос остаётся; лок снят
async def test_focusReplacedPendingKept() -> None:
    lock, store = InMemoryUserLock(), InMemoryContextStore()
    other = ItemBrief.fromWire({**ITEM, "id": "2b8d4e61-0c3f-4a7b-8e95-1f6a2d3c4b57"})
    store.contexts[USER] = DialogContext(focus=(other,), pending={"kind": "time"})
    await handler(lock, store)(request())
    saved = store.contexts[USER]
    assert [str(b.id) for b in saved.focus] == [str(ITEM["id"])]
    assert saved.pending == {"kind": "time"}
    assert (lock.acquired, lock.released, lock.held) == (1, 1, {})


# Примечание: пользователь занят ходом — 503 upstream_unavailable, контекст не тронут
async def test_lockBusy() -> None:
    lock, store = InMemoryUserLock(), InMemoryContextStore()
    lock.held[USER] = "someone"
    with pytest.raises(AppError) as caught:
        await handler(lock, store)(request())
    assert caught.value.code == ErrorCode.UPSTREAM_UNAVAILABLE
    assert caught.value.httpStatus == 503
    assert store.contexts == {}


# Примечание: сбой записи контекста — ошибка наружу (бот запишет warning), лок всё равно снят
async def test_saveFailureReleasesLock() -> None:
    lock = InMemoryUserLock()
    with pytest.raises(ConnectionError):
        await handler(lock, InMemoryContextStore(failSave=True))(request())
    assert lock.held == {}
