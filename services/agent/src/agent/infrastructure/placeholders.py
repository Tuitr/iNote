"""ВРЕМЕННЫЕ адаптеры S1, шаг 1: каждый заменяется на своём шаге и удаляется.

- DevUserResolver → HttpUserResolver (J8), шаг 4;
- PlaceholderBrain → ScriptedBrain / Brain B через AGENT_BRAIN, шаг 2;
- NoToolsExecutorFactory → McpToolExecutor, шаг 3.
"""

from collections.abc import Mapping

from agent.domain.user import Caller, UserContext
from contracts.agent_side import (
    ErrorInfo,
    ToolExecutor,
    ToolResult,
    ToolSpec,
    TurnInput,
    TurnResult,
    Usage,
)


# Примечание: (временно, до шага 4) пользователь без J8: user_id = max_user_id, пояс по умолчанию
class DevUserResolver:
    # Примечание: пояс — DEFAULT_TZ из настроек
    def __init__(self, defaultTz: str) -> None:
        self._defaultTz = defaultTz

    # Примечание: детерминированно, без сети
    async def resolve(self, maxUserId: int, maxChatId: int, name: str | None) -> UserContext:
        return UserContext(
            userId=maxUserId, maxUserId=maxUserId, maxChatId=maxChatId, tz=self._defaultTz
        )


# Примечание: (временно, до шага 2) Brain без правил — честно отвечает, что ещё не подключён
class PlaceholderBrain:
    # Примечание: ход без инструментов, outcome=answer
    async def run(self, turn: TurnInput, tools: ToolExecutor) -> TurnResult:
        return TurnResult(
            outcome="answer",
            say="Агент на связи, но понимать фразы научится на следующем шаге.",
            usage=Usage(model="placeholder", llmCalls=0, tokensIn=0, tokensOut=0, latencyMs=0),
        )

    # Примечание: уточнения — S2
    async def resume(self, turn: TurnInput, choice: str, tools: ToolExecutor) -> TurnResult:
        raise NotImplementedError("resume — S2")


# Примечание: (временно, до шага 3) инструментов нет
class _NoTools:
    # Примечание: пустой список инструментов
    async def listTools(self) -> list[ToolSpec]:
        return []

    # Примечание: любой вызов — ошибка «недоступно»
    async def call(self, name: str, args: Mapping[str, object]) -> ToolResult:
        return ToolResult(
            ok=False,
            summary="Инструменты ещё не подключены",
            error=ErrorInfo(code="upstream_unavailable", message="MCP не подключён"),
        )


# Примечание: (временно, до шага 3) фабрика исполнителя без инструментов
class NoToolsExecutorFactory:
    # Примечание: caller пока не используется
    def forTurn(self, caller: Caller) -> ToolExecutor:
        return _NoTools()
