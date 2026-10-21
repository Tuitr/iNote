from fastapi import APIRouter, Request

from agent.application.use_cases import HandleAction, HandleMessage
from contracts.reply import ActionRequest, MessageRequest, Reply


# Примечание: use case из app.state — собран в lifespan
def _messageHandler(request: Request) -> HandleMessage:
    handler: HandleMessage = request.app.state.handleMessage
    return handler


# Примечание: use case из app.state — собран в lifespan
def _actionHandler(request: Request) -> HandleAction:
    handler: HandleAction = request.app.state.handleAction
    return handler


# Примечание: API агента для бота; защищено сервисным токеном (/v1/ в protectedPrefixes)
def buildAgentRouter() -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["agent"])

    # Примечание: текст пользователя → Reply
    @router.post("/messages")
    async def messages(request: Request, body: MessageRequest) -> Reply:
        return await _messageHandler(request)(body)

    # Примечание: кнопка или команда → Reply без LLM
    @router.post("/actions")
    async def actions(request: Request, body: ActionRequest) -> Reply:
        return await _actionHandler(request)(body)

    return router
