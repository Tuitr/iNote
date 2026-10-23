"""Composition root бота: lifespan собирает зависимости, больше ничего.

Запуск: uvicorn bot.main:createApp --factory --host 0.0.0.0 --port 8000
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Final

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from bot.application.deliver_notification import DeliverNotification
from bot.application.handle_update import BotDeps, HandleUpdate
from bot.application.reply_mapper import ReplyToMaxMapper
from bot.application.typing import TypingIndicator
from bot.application.update_dispatcher import UpdateDispatcher
from bot.infrastructure.audio_downloader import HttpAudioDownloader
from bot.infrastructure.fake_max import FakeMaxGateway, FakeMaxOptions
from bot.infrastructure.http_agent import HttpAgentGateway
from bot.infrastructure.http_asr import HttpAsrGateway
from bot.infrastructure.http_focus import HttpFocusNotifier
from bot.infrastructure.max_mapper import mapRawUpdate
from bot.infrastructure.max_throttle import ThrottledMaxGateway
from bot.infrastructure.memory_marker import InMemoryMarkerStore
from bot.infrastructure.real_max import createRealMaxGateway
from bot.infrastructure.redis_adapters import (
    RedisCallbackStore,
    RedisNotificationLedger,
    RedisRateLimiter,
    RedisUpdateDeduplicator,
)
from bot.infrastructure.settings import BotSettings
from bot.presentation.dev_routes import buildDevRouter
from bot.presentation.notify_routes import buildNotifyRouter
from bot.presentation.polling import PollingLoop
from bot.presentation.templates import RussianNotificationTexts, RussianTexts
from bot.presentation.webhook import buildWebhookRouter
from common.errors import installErrorHandlers
from common.health import HealthCheck, buildHealthRouter
from common.http import RetryPolicy, ServiceHttpClient
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
    asrTransport: httpx.AsyncBaseTransport | None = None,
    filesTransport: httpx.AsyncBaseTransport | None = None,
    webhookTransport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    config = settings or BotSettings()
    configureLogging(SERVICE_NAME, config.logLevel, config.logFormat)

    # Примечание: клиенты и фоновые задачи живут ровно столько, сколько приложение (правило 9)
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if config.maxMode == "real":
            createRealMaxGateway()
        redis = redisFactory(config.redisUrl)
        fake = FakeMaxGateway(
            FakeMaxOptions(
                sendDelayS=config.fakeSendDelayMs / 1000, filesBaseUrl=config.fakeFilesUrl
            )
        )
        # все запросы к MAX — через клиентский лимит 30 в секунду (и у реального адаптера)
        gateway = ThrottledMaxGateway(fake)
        store = KeyedRedis(redis)
        # Бот → агент: 45 с без повторов (стандарт); POST без ключа и так не повторяется
        agentHttp = ServiceHttpClient(
            baseUrl=config.agentUrl,
            timeoutS=config.agentTimeoutS,
            serviceToken=config.serviceToken,
            upstreamName="agent",
            transport=agentTransport,
        )
        # ASR (J1): 15 с и до 2 повторов — вызов идемпотентный (ключ — хеш аудио)
        asrHttp = ServiceHttpClient(
            baseUrl=config.asrUrl,
            timeoutS=config.asrTimeoutS,
            serviceToken=config.serviceToken,
            retry=RetryPolicy(retries=2),
            upstreamName="asr",
            transport=asrTransport,
        )
        # Голосовые — по внешней ссылке MAX: без сервисного токена, редиректы допустимы
        filesHttp = httpx.AsyncClient(
            timeout=config.voiceDownloadTimeoutS, follow_redirects=True, transport=filesTransport
        )
        # фейк в режиме webhook стучится на свой адрес подписки (в тестах — ASGI-транспорт)
        webhookClient = (
            httpx.AsyncClient(transport=webhookTransport)
            if config.updatesMode == "webhook" and config.maxMode == "fake"
            else None
        )
        callbacks = RedisCallbackStore(store)
        app.state.devConsole = fake
        app.state.maxGateway = gateway
        app.state.redis = redis
        app.state.agentHttp = agentHttp
        try:
            mapper = ReplyToMaxMapper(
                callbacks, mode=config.openAppMode, botUsername=config.username
            )
            async with managedTasks(config.shutdownTimeoutS) as tasks:
                handler = HandleUpdate(
                    BotDeps(
                        gateway=gateway,
                        deduplicator=RedisUpdateDeduplicator(store),
                        rateLimiter=RedisRateLimiter(store, limit=config.rateLimitPerMin),
                        texts=RussianTexts(),
                        agent=HttpAgentGateway(agentHttp),
                        callbacks=callbacks,
                        mapper=mapper,
                        typing=TypingIndicator(gateway, tasks, config.typingIntervalS),
                        downloader=HttpAudioDownloader(
                            filesHttp,
                            maxBytes=config.voiceMaxBytes,
                            httpsOnly=config.maxMode == "real",
                        ),
                        asr=HttpAsrGateway(asrHttp),
                    )
                )
                app.state.deliverNotification = DeliverNotification(
                    ledger=RedisNotificationLedger(store),
                    gateway=gateway,
                    mapper=mapper,
                    texts=RussianNotificationTexts(),
                    focus=HttpFocusNotifier(agentHttp, tasks),
                )
                dispatcher = UpdateDispatcher(handler, tasks, config.maxConcurrentUpdates)
                app.state.dispatcher = dispatcher
                if config.updatesMode == "webhook":
                    # фейк доставляет апдейты POST-ом на свой же /webhook/max — как MAX
                    if webhookClient is not None:
                        fake.attachWebhook(webhookClient, tasks)
                    await gateway.ensureSubscription(
                        config.webhookUrl, config.webhookSecret.get_secret_value()
                    )
                else:
                    # Фейковый MAX теряет очередь при рестарте — его marker в Redis хранить
                    # нельзя; для real здесь будет RedisMarkerStore(store)
                    loop = PollingLoop(
                        gateway=gateway,
                        dispatcher=dispatcher,
                        markerStore=InMemoryMarkerStore(),
                        pollTimeoutS=config.pollTimeoutS,
                    )
                    _ = tasks.spawn(loop.run(tasks.stopping), name=POLLING_TASK)
                _log.info("bot_started", max_mode=config.maxMode, updates_mode=config.updatesMode)
                yield
                _log.info("bot_stopping")
        finally:
            await agentHttp.aclose()
            await asrHttp.aclose()
            await filesHttp.aclose()
            if webhookClient is not None:
                await webhookClient.aclose()
            await redis.aclose()
            _log.info("bot_stopped")

    app = FastAPI(title="va-bot", lifespan=lifespan)
    installErrorHandlers(app)
    installMiddleware(app, serviceToken=config.serviceToken)
    _includeRouters(app, config)
    return app


# Примечание: роуты: health и J4 — всегда, webhook — в режиме webhook, /dev/max/* — с фейком
def _includeRouters(app: FastAPI, config: BotSettings) -> None:
    # Готов, когда доступны Redis и агент (план зоны C); MAX в readiness не дёргаем
    app.include_router(buildHealthRouter({"redis": _redisCheck(app), "agent": _agentCheck(app)}))
    app.include_router(buildNotifyRouter())
    if config.updatesMode == "webhook":
        app.include_router(
            buildWebhookRouter(config.webhookSecret.get_secret_value(), mapRawUpdate)
        )
    if config.maxMode == "fake":
        app.include_router(buildDevRouter())


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
