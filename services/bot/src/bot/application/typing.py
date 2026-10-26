"""«Печатает…», пока агент думает: typing_on раз в несколько секунд; повторы — в TaskRegistry."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Final

from bot.application.ports import MaxGateway
from bot.domain.updates import MaxChatId
from common.lifecycle import ShuttingDownError, TaskRegistry
from common.logging import getLogger
from common.trace import currentTraceId

# Индикатор MAX гаснет сам через несколько секунд — обновляем чаще
DEFAULT_INTERVAL_S: Final = 4.0
TASK_NAME: Final = "typing"

_log = getLogger(__name__)


# Примечание: индикатор набора на время блока; сбой индикатора ответ не ломает
class TypingIndicator:
    # Примечание: tasks — реестр фоновых задач бота, intervalS — как часто повторять typing_on
    def __init__(
        self, gateway: MaxGateway, tasks: TaskRegistry, intervalS: float = DEFAULT_INTERVAL_S
    ) -> None:
        self._gateway = gateway
        self._tasks = tasks
        self._intervalS = intervalS

    # Примечание: async with typing.during(chat): … — первый typing_on сразу, повторы — в фоне
    @asynccontextmanager
    async def during(self, chatId: MaxChatId) -> AsyncIterator[None]:
        await self._sendOnce(chatId)
        try:
            # имя задачи попадает в лог при остановке — без chat_id MAX (правило 10)
            name = f"{TASK_NAME}:{currentTraceId() or 'no-trace'}"
            task = self._tasks.spawn(self._repeat(chatId), name=name)
        except ShuttingDownError:
            # Бот останавливается: ответ доделаем, а повторять индикатор незачем
            yield
            return
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    # Примечание: повтор раз в intervalS, пока блок не закончится
    async def _repeat(self, chatId: MaxChatId) -> None:
        while True:
            await asyncio.sleep(self._intervalS)
            await self._sendOnce(chatId)

    # Примечание: один typing_on; ошибка MAX — в debug: индикатор необязателен
    async def _sendOnce(self, chatId: MaxChatId) -> None:
        try:
            await self._gateway.sendTyping(chatId)
        except Exception as exc:  # noqa: BLE001
            _log.debug("typing_failed", error=type(exc).__name__)
