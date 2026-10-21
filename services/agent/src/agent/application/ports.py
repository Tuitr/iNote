from typing import Protocol

from contracts.reply import ActionRequest, MessageRequest, Reply


# Примечание: источник ответа; в S0 — фиксированные примеры, в S1 — Brain + MCP + шаблоны
class ReplySource(Protocol):
    # Примечание: ответ на текст пользователя
    async def forMessage(self, request: MessageRequest) -> Reply: ...

    # Примечание: ответ на кнопку или команду без LLM
    async def forAction(self, request: ActionRequest) -> Reply: ...
