"""Фейки портов агента для тестов. Brain — TurnResult по сценарию (ScriptedBrain — шаг 2)."""

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Final

import httpx

from agent.application.ports import DialogContext
from agent.domain.user import Caller, UserContext, UserId
from common.errors import AppError
from contracts.agent_side import (
    ErrorInfo,
    ExecutedAction,
    ToolExecutor,
    ToolResult,
    ToolSpec,
    TurnInput,
    TurnResult,
    Usage,
)

USAGE: Final = Usage(model="test", llmCalls=1, tokensIn=1, tokensOut=1, latencyMs=1)
ITEM_ID: Final = "7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90"
ITEM: Final[dict[str, object]] = {
    "id": ITEM_ID,
    "kind": "reminder",
    "title": "Позвонить Ивану",
    "starts_at": "2026-10-22T10:00:00+03:00",
    "all_day": False,
    "status": "open",
}


# Примечание: успешный вызов изменяющего инструмента с записью в data
def createdAction() -> ExecutedAction:
    return ExecutedAction(
        name="create_task",
        args={"title": "Позвонить Ивану"},
        result=ToolResult(ok=True, summary="Создано", data={"status": "created", "item": ITEM}),
    )


# Примечание: пользователь J8 с фиксированным id и поясом
class FakeUserResolver:
    # Примечание: error — имитировать недоступность J8
    def __init__(self, tz: str = "Europe/Moscow", error: AppError | None = None) -> None:
        self.tz = tz
        self.error = error
        self.calls: list[tuple[int, int, str | None]] = []

    # Примечание: user_id = max_user_id + 1 — чтобы не спутать их в проверках
    async def resolve(self, maxUserId: int, maxChatId: int, name: str | None) -> UserContext:
        self.calls.append((maxUserId, maxChatId, name))
        if self.error is not None:
            raise self.error
        return UserContext(
            userId=maxUserId + 1, maxUserId=maxUserId, maxChatId=maxChatId, tz=self.tz
        )


# Примечание: лок в памяти с той же семантикой токена, что у Redis
class InMemoryUserLock:
    # Примечание: held — кто держит лок сейчас
    def __init__(self) -> None:
        self.held: dict[UserId, str] = {}
        self.acquired = 0
        self.released = 0

    # Примечание: занят — ждём до waitS, проверяя раз в 10 мс
    async def acquire(self, userId: UserId, waitS: float) -> str | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + waitS
        while userId in self.held:
            if loop.time() >= deadline:
                return None
            await asyncio.sleep(0.01)
        self.acquired += 1
        token = f"token-{self.acquired}"
        self.held[userId] = token
        return token

    # Примечание: снимает только свой токен
    async def release(self, userId: UserId, token: str) -> bool:
        if self.held.get(userId) != token:
            return False
        del self.held[userId]
        self.released += 1
        return True


# Примечание: контекст диалога в памяти; failSave — имитация сбоя Redis
class InMemoryContextStore:
    # Примечание: contexts — что сохранено по пользователю
    def __init__(self, failSave: bool = False) -> None:
        self.contexts: dict[UserId, DialogContext] = {}
        self.failSave = failSave

    # Примечание: пустой контекст по умолчанию
    async def load(self, userId: UserId) -> DialogContext:
        return self.contexts.get(userId, DialogContext())

    # Примечание: запоминает или падает
    async def save(self, userId: UserId, context: DialogContext) -> None:
        if self.failSave:
            msg = "redis down"
            raise ConnectionError(msg)
        self.contexts[userId] = context


# Примечание: замороженные часы
class FixedClock:
    # Примечание: момент задаётся тестом
    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    # Примечание: всегда один и тот же момент
    def now(self) -> datetime:
        return self.moment


# Примечание: исполнитель хода: запоминает caller и вызовы, отвечает заготовкой по имени
class RecordingToolExecutor:
    # Примечание: caller приходит от фабрики; общие с фабрикой calls и responses
    def __init__(
        self,
        caller: Caller,
        calls: list[tuple[str, dict[str, object]]],
        responses: Mapping[str, ToolResult],
    ) -> None:
        self.caller = caller
        self._calls = calls
        self._responses = responses

    # Примечание: инструментов нет — сценарий Brain задаёт тест
    async def listTools(self) -> list[ToolSpec]:
        return []

    # Примечание: ответ из responses; не задан — ошибка internal
    async def call(self, name: str, args: Mapping[str, object]) -> ToolResult:
        self._calls.append((name, dict(args)))
        missing = ToolResult(ok=False, summary="", error=ErrorInfo(code="internal", message="нет"))
        return self._responses.get(name, missing)


# Примечание: фабрика, запоминающая caller каждого хода и все вызовы инструментов
class RecordingToolFactory:
    # Примечание: callers — по одному на ход; responses — ответы инструментов по имени
    def __init__(self, responses: Mapping[str, ToolResult] | None = None) -> None:
        self.callers: list[Caller] = []
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.responses: dict[str, ToolResult] = dict(responses or {})

    # Примечание: новый исполнитель на ход
    def forTurn(self, caller: Caller) -> ToolExecutor:
        self.callers.append(caller)
        return RecordingToolExecutor(caller, self.calls, self.responses)


# Примечание: успешный ответ изменяющего инструмента со статусом и записью
def mutation(status: str, item: Mapping[str, object] | None = None) -> ToolResult:
    data: dict[str, object] = {"status": status, "item": dict(item or ITEM)}
    return ToolResult(ok=True, summary=status, data=data)


# Примечание: ответ-ошибка инструмента с кодом стандарта
def toolError(code: str) -> ToolResult:
    return ToolResult(ok=False, summary=code, error=ErrorInfo(code=code, message=code))


type Script = Callable[[TurnInput], TurnResult]


# Примечание: Brain по сценарию теста: возвращает заданный TurnResult, может ждать или падать
class ScenarioBrain:
    # Примечание: delayS — долгий ход; error — исключение из Brain
    def __init__(self, script: Script, delayS: float = 0.0, error: Exception | None = None) -> None:
        self.script = script
        self.delayS = delayS
        self.error = error
        self.turns: list[TurnInput] = []
        self.choices: list[str] = []

    # Примечание: запоминает TurnInput — проверяем, что в нём нет user_id
    async def run(self, turn: TurnInput, tools: ToolExecutor) -> TurnResult:
        self.turns.append(turn)
        await asyncio.sleep(self.delayS)
        if self.error is not None:
            raise self.error
        return self.script(turn)

    # Примечание: resume — тот же сценарий; choices — какие варианты выбирали
    async def resume(self, turn: TurnInput, choice: str, tools: ToolExecutor) -> TurnResult:
        self.choices.append(choice)
        self.turns.append(turn)
        return self.script(turn)


# Примечание: служебный REST mcp-контейнера (J8 и health) для httpx.MockTransport; requests — журнал
class FakeMcpHttp:
    # Примечание: down — resolve всегда 503; failures — первые n ответов 503; body — свой ответ
    def __init__(
        self,
        userId: int = 17,
        tz: str = "Europe/Moscow",
        *,
        down: bool = False,
        failures: int = 0,
        body: dict[str, object] | None = None,
    ) -> None:
        self.userId = userId
        self.tz = tz
        self.down = down
        self.failures = failures
        self.body = body
        self.healthy = True
        self.requests: list[httpx.Request] = []

    # Примечание: транспорт для ServiceHttpClient / AgentOverrides.mcpTransport
    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    # Примечание: запросы resolve
    @property
    def resolves(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path == "/internal/v1/users/resolve"]

    # Примечание: /health/live и /internal/v1/users/resolve; остальное — 404
    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/health/live":
            return httpx.Response(200 if self.healthy else 503, json={"status": "ok"})
        if request.url.path != "/internal/v1/users/resolve":
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "-"}})
        if self.down or self.failures > 0:
            self.failures -= 1
            error = {"code": "upstream_unavailable", "message": "down", "details": {}}
            return httpx.Response(503, json={"error": error})
        if self.body is not None:
            return httpx.Response(200, json=self.body)
        user = {"user_id": self.userId, "tz": self.tz, "settings": {}, "created": True}
        return httpx.Response(200, json=user)
