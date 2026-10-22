from fastapi import APIRouter, Request, Response
from pydantic import Field

from bot.application.dev_console import DevMaxConsole
from common.errors import AppError, ErrorCode
from common.wire import WireModel


# Примечание: ответ на подложенный апдейт
class PushedUpdate(WireModel):
    accepted: bool
    position: int


# Примечание: голосовое «от пользователя»: текст станет фейковым аудио OggS + текст
class PushVoice(WireModel):
    maxUserId: int = Field(gt=0)
    maxChatId: int
    text: str = Field(max_length=4000)


# Примечание: одно отправленное ботом сообщение
class SentMessageDto(WireModel):
    messageId: str
    chatId: int
    text: str
    format: str
    attachments: list[dict[str, object]]


# Примечание: один ответ на нажатие кнопки
class CallbackAnswerDto(WireModel):
    callbackId: str
    notification: str | None


# Примечание: подписка webhook (секрет не показываем)
class SubscriptionDto(WireModel):
    url: str
    hasSecret: bool
    updateTypes: list[str]


# Примечание: журнал фейкового MAX
class SentLog(WireModel):
    messages: list[SentMessageDto]
    callbackAnswers: list[CallbackAnswerDto]
    typing: list[int]


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

    # Примечание: голосовое: файл в памяти фейка + апдейт со ссылкой на /dev/max/files/{id}
    @router.post("/voice")
    async def pushVoice(request: Request, body: PushVoice) -> PushedUpdate:
        position = await _console(request).pushVoice(body.maxUserId, body.maxChatId, body.text)
        return PushedUpdate(accepted=True, position=position)

    # Примечание: скачивание голосового по ссылке из апдейта — как CDN MAX
    @router.get("/files/{fileId}")
    async def voiceFile(request: Request, fileId: str) -> Response:
        voice = _console(request).voiceFile(fileId)
        if voice is None:
            raise AppError(ErrorCode.NOT_FOUND, "файла нет")
        return Response(content=voice.content, media_type=voice.mime)

    # Примечание: подписки webhook, которые бот оформил при старте (ensureSubscription)
    @router.get("/subscriptions")
    async def subscriptions(request: Request) -> list[SubscriptionDto]:
        return [
            SubscriptionDto(url=s.url, hasSecret=s.hasSecret, updateTypes=list(s.updateTypes))
            for s in _console(request).subscriptions()
        ]

    # Примечание: что бот отправил — сообщения и ответы на кнопки
    @router.get("/sent")
    async def sent(request: Request) -> SentLog:
        console = _console(request)
        return SentLog(
            messages=[
                SentMessageDto(
                    messageId=m.messageId,
                    chatId=m.chatId,
                    text=m.text,
                    format=m.format,
                    attachments=m.attachments,
                )
                for m in console.sentMessages()
            ],
            callbackAnswers=[
                CallbackAnswerDto(callbackId=a.callbackId, notification=a.notification)
                for a in console.callbackAnswers()
            ],
            typing=list(console.typingActions()),
        )

    return router
