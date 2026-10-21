"""Composition root агента: lifespan собирает зависимости, больше ничего.

Запуск: uvicorn agent.main:createApp --factory --host 0.0.0.0 --port 8000
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final

from fastapi import FastAPI
from redis.asyncio import Redis

from agent.application.handle_message import HandleMessage, TurnDeps
from agent.application.ports import Clock, ReplyRenderer, ToolExecutorFactory, UserResolver
from agent.application.use_cases import HandleAction
from agent.infrastructure.brains import buildBrain
from agent.infrastructure.placeholders import DevUserResolver, NoToolsExecutorFactory
from agent.infrastructure.redis_context import RedisDialogContextStore
from agent.infrastructure.redis_lock import RedisUserLock
from agent.infrastructure.settings import AgentSettings
from agent.infrastructure.stub_replies import ExampleReplySource, loadReplyExample
from agent.infrastructure.system_clock import SystemClock
from agent.presentation.basic_renderer import BasicReplyRenderer
from agent.presentation.routes import buildAgentRouter
from common.errors import installErrorHandlers
from common.health import HealthCheck, buildHealthRouter
from common.logging import configureLogging, getLogger
from common.middleware import INTERNAL_PREFIXES, installMiddleware
from common.redis import KeyedRedis, createRedis
from contracts.agent_side import Brain

SERVICE_NAME: Final = "agent"
# Агент зовёт только бот: и /v1/*, и /internal/* — под сервисным токеном
PROTECTED_PREFIXES: Final = (*INTERNAL_PREFIXES, "/v1/")

type RedisFactory = Callable[[str], Redis]

_log = getLogger(__name__)


# Примечание: подмены для тестов; None — штатная реализация из настроек
@dataclass(frozen=True)
class AgentOverrides:
    redisFactory: RedisFactory | None = None
    brain: Brain | None = None
    resolver: UserResolver | None = None
    tools: ToolExecutorFactory | None = None
    clock: Clock | None = None
    renderer: ReplyRenderer | None = None


# Примечание: фабрика приложения; зависимости собираются один раз в lifespan (правило 9)
def createApp(
    settings: AgentSettings | None = None, overrides: AgentOverrides | None = None
) -> FastAPI:
    config = settings or AgentSettings()
    custom = overrides or AgentOverrides()
    configureLogging(SERVICE_NAME, config.logLevel, config.logFormat)

    # Примечание: Redis, ход агента и заглушка /v1/actions; клиенты закрываются при остановке
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        brain = custom.brain or buildBrain(config.brain)
        actionSource = ExampleReplySource(loadReplyExample(config.stubActionReply))
        redis = (custom.redisFactory or createRedis)(config.redisUrl)
        store = KeyedRedis(redis)
        deps = TurnDeps(
            resolver=custom.resolver or DevUserResolver(config.defaultTz),
            lock=RedisUserLock(store),
            contextStore=RedisDialogContextStore(store),
            clock=custom.clock or SystemClock(),
            brain=brain,
            tools=custom.tools or NoToolsExecutorFactory(),
            renderer=custom.renderer or BasicReplyRenderer(),
        )
        app.state.redis = redis
        app.state.handleMessage = HandleMessage(
            deps, turnTimeoutS=config.turnTimeoutS, lockWaitS=config.lockWaitS
        )
        app.state.handleAction = HandleAction(actionSource)
        _log.info("agent_started", brain=config.brain if custom.brain is None else "override")
        try:
            yield
        finally:
            _log.info("agent_stopping")
            await redis.aclose()
            _log.info("agent_stopped")

    app = FastAPI(title="va-agent", lifespan=lifespan)
    installErrorHandlers(app)
    installMiddleware(app, serviceToken=config.serviceToken, protectedPrefixes=PROTECTED_PREFIXES)
    # LLM и ASR в readiness не дёргаем (правило 9); пинг MCP-контейнера — шаг 4
    app.include_router(buildHealthRouter({"redis": _redisCheck(app)}))
    app.include_router(buildAgentRouter())
    return app


# Примечание: клиент Redis появляется в lifespan, поэтому проверка берёт его из app.state при вызове
def _redisCheck(app: FastAPI) -> HealthCheck:
    # Примечание: PING текущего клиента; до старта lifespan — ошибка, то есть «не готов»
    async def probe() -> object:
        redis: Redis = app.state.redis
        return await redis.ping()

    return HealthCheck(probe=probe)
