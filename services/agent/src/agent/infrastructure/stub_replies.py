from pathlib import Path

from contracts.reply import ActionRequest, MessageRequest, Reply


# Примечание: читает Reply из contracts/examples и проверяет его моделью — кривой пример не стартует
def loadReplyExample(path: Path) -> Reply:
    return Reply.fromJson(path.read_bytes())


# Примечание: заглушка S0: на любой запрос — заранее загруженный пример
class ExampleReplySource:
    # Примечание: ответы загружаются один раз на старте (lifespan)
    def __init__(self, messageReply: Reply, actionReply: Reply) -> None:
        self._messageReply = messageReply
        self._actionReply = actionReply

    # Примечание: фиксированный ответ на сообщение
    async def forMessage(self, request: MessageRequest) -> Reply:
        return self._messageReply

    # Примечание: фиксированный ответ на действие
    async def forAction(self, request: ActionRequest) -> Reply:
        return self._actionReply
