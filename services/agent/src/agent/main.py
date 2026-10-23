"""Composition root агента: lifespan собирает зависимости, больше ничего.

Запуск: uvicorn agent.main:createApp --factory --host 0.0.0.0 --port 8000
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Final

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from agent.application.handle_action import HandleAction
from agent.application.handle_focus import HandleFocus
from agent.application.handle_message import HandleMessage
from agent.application.ports import (
    Clock,
    DialogLog,
    ReplyRenderer,
    ToolExecutorFactory,
    UserResolver,
)
from agent.application.turn_pipeline import TurnDeps, TurnPipeline
from agent.infrastructure.brains import buildBrain
from agent.infrastructure.http_dialog_log import TASK_PREFIX as DIALOG_TASK_PREFIX
from agent.infrastructure.http_dialog_log import HttpDialogLogWriter
from agent.infrastructure.http_user_resolver import HttpUserResolver
from agent.infrastructure.mcp_connection import McpConnection, httpSessionFactory
from agent.infrastructure.mcp_executor import McpToolExecutorFactory
from agent.infrastructure.redis_context import RedisDialogContextStore
from agent.infrastructure.redis_lock import RedisUserLock
from agent.infrastructure.settings import AgentSettings
from agent.infrastructure.system_clock import SystemClock
from agent.presentation.reply_renderer import TemplateReplyRenderer
from agent.presentation.routes import buildAgentRouter
from agent.presentation.templates import loadTemplates
from common.errors import installErrorHandlers
from common.health import HealthCheck, buildHealthRouter
from common.http import RetryPolicy, ServiceHttpClient
from common.lifecycle import managedTasks
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
    dialogLog: DialogLog | None = None
    # транспорт к служебному REST mcp-контейнера (J8, health) — httpx.MockTransport в тестах
    mcpTransport: httpx.AsyncBaseTransport | None = None


# Примечание: фабрика приложения; зависимости собираются один раз в lifespan (правило 9)
def createApp(
    settings: AgentSettings | None = None, overrides: AgentOverrides | None = None
) -> FastAPI:
    config = settings or AgentSettings()
    custom = overrides or AgentOverrides()
    configureLogging(SERVICE_NAME, config.logLevel, config.logFormat)

    # Примечание: Redis, MCP-сессия, ход и действия; всё закрывается при остановке
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        brain = custom.brain or buildBrain(config.brain)
        # Шаблоны проверяются здесь: опечатка в ru.toml — агент не стартует
        templates = loadTemplates()
        clock = custom.clock or SystemClock()
        async with managedTasks() as tasks:
            redis = (custom.redisFactory or createRedis)(config.redisUrl)
            store = KeyedRedis(redis)
            # J8 идемпотентен по max_user_id: таймаут и 1 повтор — как у MCP
            mcpHttp = ServiceHttpClient(
                baseUrl=config.mcpInternalUrl,
                timeoutS=config.mcpTimeoutS,
                serviceToken=config.serviceToken,
                retry=RetryPolicy(retries=1),
                upstreamName="mcp",
                transport=custom.mcpTransport,
            )
            mcp: McpConnection | None = None
            tools = custom.tools
            if tools is None:
                mcp = McpConnection(httpSessionFactory(config.mcpUrl, config.serviceToken))
                mcp.start(tasks)
                tools = McpToolExecutorFactory(mcp, timeoutS=config.mcpTimeoutS)
            # J8 dialog-turns идемпотентен по trace_id: 2 повтора, ответ пользователю не ждёт
            dialogHttp = ServiceHttpClient(
                baseUrl=config.mcpInternalUrl,
                timeoutS=config.mcpTimeoutS,
                serviceToken=config.serviceToken,
                retry=RetryPolicy(retries=2),
                upstreamName="mcp",
                transport=custom.mcpTransport,
            )
            deps = TurnDeps(
                resolver=custom.resolver or HttpUserResolver(mcpHttp, store),
                lock=RedisUserLock(store),
                contextStore=RedisDialogContextStore(store),
                clock=clock,
                brain=brain,
                tools=tools,
                renderer=custom.renderer or TemplateReplyRenderer(templates, clock),
                dialogLog=custom.dialogLog or HttpDialogLogWriter(dialogHttp, tasks),
            )
            app.state.redis = redis
            app.state.mcpHttp = mcpHttp
            pipeline = TurnPipeline(
                deps, turnTimeoutS=config.turnTimeoutS, lockWaitS=config.lockWaitS
            )
            app.state.handleMessage = HandleMessage(pipeline)
            app.state.handleAction = HandleAction(pipeline)
            app.state.handleFocus = HandleFocus(deps, lockWaitS=config.lockWaitS)
            _log.info("agent_started", brain=config.brain if custom.brain is None else "override")
            try:
                yield
            finally:
                _log.info("agent_stopping")
                # MCP-сессию закрываем до остановки реестра: её хозяин — фоновая задача в нём
                if mcp is not None:
                    await mcp.close()
                # затем дожидаемся записей истории диалога — им ещё нужен HTTP-клиент
                report = await tasks.shutdown()
                lost = [n for n in report.cancelled if n.startswith(f"{DIALOG_TASK_PREFIX}:")]
                if lost:
                    _log.warning("dialog_turns_lost", tasks=lost, reason="shutdown_timeout")
                await dialogHttp.aclose()
                await mcpHttp.aclose()
                await redis.aclose()
                _log.info("agent_stopped")

    app = FastAPI(title="va-agent", lifespan=lifespan)
    installErrorHandlers(app)
    installMiddleware(app, serviceToken=config.serviceToken, protectedPrefixes=PROTECTED_PREFIXES)
    # LLM и ASR в readiness не дёргаем (правило 9); MCP-контейнер — пинг /health/live
    app.include_router(buildHealthRouter({"redis": _redisCheck(app), "mcp": _mcpCheck(app)}))
    app.include_router(buildAgentRouter())
    return app


# Примечание: клиент Redis появляется в lifespan, поэтому проверка берёт его из app.state при вызове
def _redisCheck(app: FastAPI) -> HealthCheck:
    # Примечание: PING текущего клиента; до старта lifespan — ошибка, то есть «не готов»
    async def probe() -> object:
        redis: Redis = app.state.redis
        return await redis.ping()

    return HealthCheck(probe=probe)


# Примечание: живой ли mcp-контейнер; без повторов — readiness должна отвечать быстро
def _mcpCheck(app: FastAPI) -> HealthCheck:
    # Примечание: GET /health/live; не-2xx и сетевой сбой — AppError, то есть «не готов»
    async def probe() -> object:
        http: ServiceHttpClient = app.state.mcpHttp
        return await http.get("/health/live", retry=False)

    return HealthCheck(probe=probe)
