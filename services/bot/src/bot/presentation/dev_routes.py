from fastapi import APIRouter, Request

from bot.application.dev_console import DevMaxConsole
from common.wire import WireModel


# Примечание: ответ на подложенный апдейт
class PushedUpdate(WireModel):
    accepted: bool
    position: int


# Примечание: одно отправленное ботом сообщение
class SentMessageDto(WireModel):
    messageId: str
    chatId: int
    text: str
    format: str


# Примечание: один ответ на нажатие кнопки
class CallbackAnswerDto(WireModel):
    callbackId: str
    notification: str | None


# Примечание: журнал фейкового MAX
class SentLog(WireModel):
    messages: list[SentMessageDto]
    callbackAnswers: list[CallbackAnswerDto]


# Примечание: консоль фейкового MAX кладётся в app.state в lifespan
def _console(request: Request) -> DevMaxConsole:
    console: DevMaxConsole = request.app.state.devConsole
    return console


# Примечание: dev-роуты только для BOT_MAX_MODE=fake; в режиме real роутер не подключается
def buildDevRouter() -> APIRouter:
    router = APIRouter(prefix="/dev/max", tags=["dev"])

    # Примечание: подложить сырой апдейт MAX (формат как в fixtures/max_updates)
    @router.post("/updates")
    async def pushUpdate(request: Request, raw: dict[str, object]) -> PushedUpdate:
        position = await _console(request).pushRaw(raw)
        return PushedUpdate(accepted=True, position=position)

    # Примечание: что бот отправил — сообщения и ответы на кнопки
    @router.get("/sent")
    async def sent(request: Request) -> SentLog:
        console = _console(request)
        return SentLog(
            messages=[
                SentMessageDto(messageId=m.messageId, chatId=m.chatId, text=m.text, format=m.format)
                for m in console.sentMessages()
            ],
            callbackAnswers=[
                CallbackAnswerDto(callbackId=a.callbackId, notification=a.notification)
                for a in console.callbackAnswers()
            ],
        )

    return router
