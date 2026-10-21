"""Composition root агента: lifespan собирает зависимости, больше ничего.

Запуск: uvicorn agent.main:createApp --factory --host 0.0.0.0 --port 8000
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

from fastapi import FastAPI

from agent.application.use_cases import HandleAction, HandleMessage
from agent.infrastructure.settings import AgentSettings
from agent.infrastructure.stub_replies import ExampleReplySource, loadReplyExample
from agent.presentation.routes import buildAgentRouter
from common.errors import installErrorHandlers
from common.health import buildHealthRouter
from common.logging import configureLogging, getLogger
from common.middleware import INTERNAL_PREFIXES, installMiddleware

SERVICE_NAME: Final = "agent"
# Агент зовёт только бот: и /v1/*, и /internal/* — под сервисным токеном
PROTECTED_PREFIXES: Final = (*INTERNAL_PREFIXES, "/v1/")

_log = getLogger(__name__)


# Примечание: фабрика приложения; настройки подменяются в тестах
def createApp(settings: AgentSettings | None = None) -> FastAPI:
    config = settings or AgentSettings()
    configureLogging(SERVICE_NAME, config.logLevel, config.logFormat)

    # Примечание: в S0 источник ответов — примеры contracts/examples; Brain, MCP и Redis — S1
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        source = ExampleReplySource(
            messageReply=loadReplyExample(config.stubMessageReply),
            actionReply=loadReplyExample(config.stubActionReply),
        )
        app.state.handleMessage = HandleMessage(source)
        app.state.handleAction = HandleAction(source)
        _log.info("agent_started", mode="stub")
        yield
        _log.info("agent_stopped")

    app = FastAPI(title="va-agent", lifespan=lifespan)
    installErrorHandlers(app)
    installMiddleware(app, serviceToken=config.serviceToken, protectedPrefixes=PROTECTED_PREFIXES)
    # Зависимостей у заглушки нет: ready = live; LLM и ASR в readiness не дёргаем (правило 9)
    app.include_router(buildHealthRouter({}))
    app.include_router(buildAgentRouter())
    return app
