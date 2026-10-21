"""ВРЕМЕННЫЙ адаптер S1: DevUserResolver → HttpUserResolver (J8) на шаге 4, затем файл удаляется."""

from agent.domain.user import UserContext


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
