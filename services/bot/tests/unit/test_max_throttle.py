"""Клиентский лимит MAX (S4): не больше 30 запросов в любую секунду при всплеске (CLAUDE.md,
«Подводные камни: bot»). Часы и сон — подменные: тест быстрый и детерминированный.
"""

import asyncio
from typing import Final, override

from bot.domain.messages import MessageId, OutgoingMessage
from bot.domain.updates import MaxChatId
from bot.infrastructure.fake_max import FakeMaxGateway
from bot.infrastructure.max_throttle import MAX_REQUESTS_PER_SECOND, ThrottledMaxGateway

BURST: Final = 100
CHAT: Final = 500600700


# Примечание: подменное время: sleep двигает часы; times — моменты, когда запрос ушёл в MAX
class FakeTime:
    # Примечание: начало — 0 с
    def __init__(self) -> None:
        self.now = 0.0

    # Примечание: «сейчас»
    def clock(self) -> float:
        return self.now

    # Примечание: сон без ожидания — только сдвиг часов и отдача управления
    async def sleep(self, seconds: float) -> None:
        self.now += max(seconds, 0.0)
        await asyncio.sleep(0)


# Примечание: шлюз-счётчик: время каждого запроса к MAX
class CountingGateway(FakeMaxGateway):
    # Примечание: time — подменные часы теста
    def __init__(self, time: FakeTime) -> None:
        super().__init__()
        self.time = time
        self.times: list[float] = []

    # Примечание: запомнить момент отправки
    @override
    async def sendMessage(self, message: OutgoingMessage) -> MessageId:
        self.times.append(self.time.clock())
        return await super().sendMessage(message)

    # Примечание: «печатает…» — тоже запрос к MAX
    @override
    async def sendTyping(self, chatId: MaxChatId) -> None:
        self.times.append(self.time.clock())
        await super().sendTyping(chatId)


# Примечание: в любом окне длиной 1 с — не больше лимита
def maxInWindow(times: list[float]) -> int:
    ordered = sorted(times)
    return max(sum(1 for t in ordered if start <= t < start + 1.0) for start in ordered)


# Примечание: всплеск 100 сообщений и «печатает…» вперемешку — ≤ 30 в секунду, все дошли
async def test_burstWithinLimit() -> None:
    time = FakeTime()
    inner = CountingGateway(time)
    gateway = ThrottledMaxGateway(inner, clock=time.clock, sleep=time.sleep)
    sends = [gateway.sendMessage(OutgoingMessage(CHAT, f"№{n}")) for n in range(BURST)]
    typing = [gateway.sendTyping(CHAT) for _ in range(BURST // 2)]
    await asyncio.gather(*sends, *typing)
    assert len(inner.times) == BURST + BURST // 2
    assert maxInWindow(inner.times) <= MAX_REQUESTS_PER_SECOND
    assert len(inner.sentMessages()) == BURST


# Примечание: под лимитом запросы не ждут
async def test_noWaitUnderLimit() -> None:
    time = FakeTime()
    inner = CountingGateway(time)
    gateway = ThrottledMaxGateway(inner, clock=time.clock, sleep=time.sleep)
    for n in range(MAX_REQUESTS_PER_SECOND):
        await gateway.sendMessage(OutgoingMessage(CHAT, f"№{n}"))
    assert time.now == 0.0
