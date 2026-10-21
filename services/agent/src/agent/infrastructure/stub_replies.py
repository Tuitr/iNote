from pathlib import Path

from contracts.reply import ActionRequest, Reply


# Примечание: читает Reply из contracts/examples и проверяет его моделью — кривой пример не стартует
def loadReplyExample(path: Path) -> Reply:
    return Reply.fromJson(path.read_bytes())


# Примечание: заглушка /v1/actions до S2: на любое действие — заранее загруженный пример
class ExampleReplySource:
    # Примечание: ответ загружается один раз на старте (lifespan)
    def __init__(self, actionReply: Reply) -> None:
        self._actionReply = actionReply

    # Примечание: фиксированный ответ на действие
    async def forAction(self, request: ActionRequest) -> Reply:
        return self._actionReply
