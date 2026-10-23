"""Клиентский лимит запросов к MAX: не больше 30 в любую секунду (CLAUDE.md, «Подводные камни»).

Обёртка над любым MaxGateway (фейк или реальный адаптер): скользящее окно в 1 с по моментам
последних запросов; сверх лимита запрос ждёт, пока самый старый из окна не выйдет из него.
Всплеск (рассылка уведомлений, очередь апдейтов) не получает от MAX 429.
"""

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Final

from bot.application.ports import MaxGateway
from bot.domain.messages import MessageId, OutgoingMessage
from bot.domain.updates import MaxChatId, UpdateBatch

MAX_REQUESTS_PER_SECOND: Final = 30
WINDOW_S: Final = 1.0


# Примечание: MaxGateway с лимитом; inner — настоящий шлюз; clock и sleep подменяются в тестах
class ThrottledMaxGateway:
    # Примечание: limit — запросов за окно; по умолчанию стандарт MAX
    def __init__(
        self,
        inner: MaxGateway,
        *,
        limit: int = MAX_REQUESTS_PER_SECOND,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.inner = inner
        self.limit = limit
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._lock = asyncio.Lock()

    # Примечание: long polling — тоже запрос к MAX
    async def fetchUpdates(self, marker: int | None, timeoutS: int) -> UpdateBatch:
        await self._permit()
        return await self.inner.fetchUpdates(marker, timeoutS)

    # Примечание: сообщение в чат
    async def sendMessage(self, message: OutgoingMessage) -> MessageId:
        await self._permit()
        return await self.inner.sendMessage(message)

    # Примечание: ответ на нажатие кнопки
    async def answerCallback(self, callbackId: str, notification: str | None) -> None:
        await self._permit()
        await self.inner.answerCallback(callbackId, notification)

    # Примечание: «печатает…»
    async def sendTyping(self, chatId: MaxChatId) -> None:
        await self._permit()
        await self.inner.sendTyping(chatId)

    # Примечание: подписка на webhook
    async def ensureSubscription(self, url: str, secret: str) -> None:
        await self._permit()
        await self.inner.ensureSubscription(url, secret)

    # Примечание: место в окне: ждём, пока самый старый запрос окна не станет старше 1 с;
    # под локом — запросы встают в очередь по порядку, окно не переполняется
    async def _permit(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._sent and now - self._sent[0] >= WINDOW_S:
                    self._sent.popleft()
                if len(self._sent) < self.limit:
                    self._sent.append(now)
                    return
                await self._sleep(WINDOW_S - (now - self._sent[0]))
