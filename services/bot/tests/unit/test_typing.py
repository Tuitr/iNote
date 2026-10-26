import asyncio
from typing import override

from bot.application.typing import TypingIndicator
from bot.domain.updates import MaxChatId
from bot.infrastructure.fake_max import FakeMaxGateway
from common.lifecycle import TaskRegistry, managedTasks


# Примечание: MAX, у которого «печатает…» всегда падает
class BrokenTyping(FakeMaxGateway):
    # Примечание: сбой сети MAX
    @override
    async def sendTyping(self, chatId: MaxChatId) -> None:
        raise ConnectionError("max down")


# Примечание: сбой индикатора не ломает блок — ответ пользователю важнее
async def test_typingFailureIgnored() -> None:
    async with managedTasks(timeoutS=1) as tasks:
        typing = TypingIndicator(BrokenTyping(), tasks, intervalS=0.01)
        async with typing.during(5):
            await asyncio.sleep(0.05)
        assert tasks.activeCount == 0


# Примечание: бот уже останавливается — первый typing_on уходит, повторов нет, блок выполняется
async def test_typingWhileShuttingDown() -> None:
    gateway = FakeMaxGateway()
    tasks = TaskRegistry()
    await tasks.shutdown(timeoutS=0.1)
    done = False
    async with TypingIndicator(gateway, tasks, intervalS=0.01).during(5):
        await asyncio.sleep(0.05)
        done = True
    assert done
    assert gateway.typingActions() == (5,)


# Примечание: S4 — имя фоновой задачи «печатает…» без chat_id MAX: при остановке имена
# недоработанных задач уходят в лог (правило 10)
async def test_typingTaskNameWithoutChatId() -> None:
    tasks = TaskRegistry()
    typing = TypingIndicator(FakeMaxGateway(), tasks, intervalS=10)
    secretChat: MaxChatId = 555444333
    async with typing.during(secretChat):
        report = await tasks.shutdown(timeoutS=0.01)
    assert report.cancelled
    assert all(str(secretChat) not in name for name in report.cancelled)
