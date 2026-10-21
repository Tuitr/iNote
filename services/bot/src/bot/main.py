"""Composition root бота: lifespan собирает зависимости, больше ничего.

Запуск: uvicorn bot.main:createApp --factory --host 0.0.0.0 --port 8000
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Final

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from bot.application.handle_update import BotDeps, HandleUpdate
from bot.application.reply_mapper import ReplyToMaxMapper
from bot.application.typing import TypingIndicator
from bot.infrastructure.fake_max import FakeMaxGateway, FakeMaxOptions
from bot.infrastructure.http_agent import HttpAgentGateway
from bot.infrastructure.memory_marker import InMemoryMarkerStore
from bot.infrastructure.real_max import createRealMaxGateway
from bot.infrastructure.redis_adapters import (
    RedisCallbackStore,
    RedisRateLimiter,
    RedisUpdateDeduplicator,
)
from bot.infrastructure.settings import BotSettings
from bot.presentation.dev_routes import buildDevRouter
from bot.presentation.polling import PollingLoop
from bot.presentation.templates import RussianTexts
from common.errors import installErrorHandlers
from common.health import HealthCheck, buildHealthRouter
from common.http import ServiceHttpClient
from common.lifecycle import managedTasks
from common.logging import configureLogging, getLogger
from common.middleware import installMiddleware
from common.redis import KeyedRedis, createRedis

SERVICE_NAME: Final = "bot"
POLLING_TASK: Final = "max-long-polling"

type RedisFactory = Callable[[str], Redis]

_log = getLogger(__name__)


# Примечание: фабрика приложения; redisFactory и agentTransport подменяются в тестах
def createApp(
    settings: BotSettings | None = None,
    *,
    redisFactory: RedisFactory = createRedis,
    agentTransport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    config = settings or BotSettings()
    configureLogging(SERVICE_NAME, config.logLevel, config.logFormat)

    # Примечание: клиенты и фоновые задачи живут ровно столько, сколько приложение (правило 9)
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if config.maxMode == "real":
            createRealMaxGateway()
        redis = redisFactory(config.redisUrl)
        gateway = FakeMaxGateway(FakeMaxOptions(sendDelayS=config.fakeSendDelayMs / 1000))
        store = KeyedRedis(redis)
        # Бот → агент: 45 с без повторов (стандарт); POST без ключа и так не повторяется
        agentHttp = ServiceHttpClient(
            baseUrl=config.agentUrl,
            timeoutS=config.agentTimeoutS,
            serviceToken=config.serviceToken,
            upstreamName="agent",
            transport=agentTransport,
        )
        callbacks = RedisCallbackStore(store)
        app.state.devConsole = gateway
        app.state.redis = redis
        app.state.agentHttp = agentHttp
        try:
            async with managedTasks(config.shutdownTimeoutS) as tasks:
                handler = HandleUpdate(
                    BotDeps(
                        gateway=gateway,
                        deduplicator=RedisUpdateDeduplicator(store),
                        rateLimiter=RedisRateLimiter(store, limit=config.rateLimitPerMin),
                        texts=RussianTexts(),
                        agent=HttpAgentGateway(agentHttp),
                        callbacks=callbacks,
                        mapper=ReplyToMaxMapper(
                            callbacks, mode=config.openAppMode, botUsername=config.username
                        ),
                        typing=TypingIndicator(gateway, tasks, config.typingIntervalS),
                    )
                )
                # Фейковый MAX теряет очередь при рестарте — его marker в Redis хранить нельзя;
                # для real здесь будет RedisMarkerStore(store)
                loop = PollingLoop(
                    gateway=gateway,
                    handler=handler,
                    markerStore=InMemoryMarkerStore(),
                    pollTimeoutS=config.pollTimeoutS,
                )
                _ = tasks.spawn(loop.run(tasks.stopping), name=POLLING_TASK)
                _log.info("bot_started", max_mode=config.maxMode)
                yield
                _log.info("bot_stopping")
        finally:
            await agentHttp.aclose()
            await redis.aclose()
            _log.info("bot_stopped")

    app = FastAPI(title="va-bot", lifespan=lifespan)
    installErrorHandlers(app)
    installMiddleware(app, serviceToken=config.serviceToken)
    # Готов, когда доступны Redis и агент (план зоны C); MAX в readiness не дёргаем
    app.include_router(buildHealthRouter({"redis": _redisCheck(app), "agent": _agentCheck(app)}))
    if config.maxMode == "fake":
        app.include_router(buildDevRouter())
    return app


# Примечание: клиент Redis появляется в lifespan, поэтому проверка берёт его из app.state при вызове
def _redisCheck(app: FastAPI) -> HealthCheck:
    # Примечание: PING текущего клиента; до старта lifespan — ошибка, то есть «не готов»
    async def probe() -> object:
        redis: Redis = app.state.redis
        return await redis.ping()

    return HealthCheck(probe=probe)


# Примечание: агент жив — GET /health/live без повторов; клиент берётся из app.state при вызове
def _agentCheck(app: FastAPI) -> HealthCheck:
    # Примечание: не-2xx и сетевой сбой — AppError, то есть «не готов»
    async def probe() -> object:
        http: ServiceHttpClient = app.state.agentHttp
        return await http.get("/health/live", retry=False)

    return HealthCheck(probe=probe)
