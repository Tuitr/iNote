"""ВРЕМЕННЫЕ адаптеры S1, шаг 1: каждый заменяется на своём шаге и удаляется.

- DevUserResolver → HttpUserResolver (J8), шаг 4;
- NoToolsExecutorFactory → McpToolExecutor, шаг 3.
"""

from collections.abc import Mapping

from agent.domain.user import Caller, UserContext
from contracts.agent_side import ErrorInfo, ToolExecutor, ToolResult, ToolSpec


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
