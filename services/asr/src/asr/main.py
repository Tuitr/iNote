"""Composition root ASR: lifespan собирает провайдер, разбор аудио и кеш, больше ничего.

Запуск: uvicorn asr.main:createApp --factory --host 0.0.0.0 --port 8000
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Final

import httpx
from fastapi import FastAPI
from redis.asyncio import Redis

from asr.application.ports import AudioProbe, SpeechToText
from asr.application.transcribe import TranscribeAudio, TranscribeLimits
from asr.infrastructure.fake_stt import FakeAudioProbe, FakeSpeechToText, fakeAudio
from asr.infrastructure.ogg_probe import OggOpusProbe
from asr.infrastructure.redis_cache import RedisTranscriptCache
from asr.infrastructure.settings import AsrSettings
from asr.infrastructure.yandex_stt import YandexSpeechKit
from asr.presentation.routes import buildTranscribeRouter
from common.errors import installErrorHandlers
from common.health import HealthCheck, buildHealthRouter
from common.logging import configureLogging, getLogger
from common.middleware import INTERNAL_PREFIXES, installMiddleware
from common.redis import KeyedRedis, createRedis

SERVICE_NAME: Final = "asr"
# /v1/transcribe зовёт только бот — под сервисным токеном, как /internal/*
PROTECTED_PREFIXES: Final = (*INTERNAL_PREFIXES, "/v1/")

type RedisFactory = Callable[[str], Redis]

_log = getLogger(__name__)


# Примечание: фабрика приложения; redisFactory и providerTransport подменяются в тестах
def createApp(
    settings: AsrSettings | None = None,
    *,
    redisFactory: RedisFactory = createRedis,
    providerTransport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    config = settings or AsrSettings()
    configureLogging(SERVICE_NAME, config.logLevel, config.logFormat)

    # Примечание: клиенты живут ровно столько, сколько приложение (правило 9)
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        redis = redisFactory(config.redisUrl)
        providerHttp = httpx.AsyncClient(transport=providerTransport)
        app.state.redis = redis
        try:
            stt, probe = _provider(config, providerHttp)
            app.state.transcribe = TranscribeAudio(
                stt,
                probe,
                RedisTranscriptCache(KeyedRedis(redis)),
                TranscribeLimits(
                    maxBytes=config.maxBytes,
                    maxDurationMs=config.maxDurationS * 1000,
                    lowConfidenceBelow=config.lowConfidenceBelow,
                ),
            )
            _log.info(
                "asr_started",
                provider=config.provider,
                max_bytes=config.maxBytes,
                max_duration_s=config.maxDurationS,
            )
            yield
            _log.info("asr_stopping")
        finally:
            await providerHttp.aclose()
            await redis.aclose()
            _log.info("asr_stopped")

    app = FastAPI(title="va-asr", lifespan=lifespan)
    installErrorHandlers(app)
    installMiddleware(app, serviceToken=config.serviceToken, protectedPrefixes=PROTECTED_PREFIXES)
    # Готов, когда доступен Redis (кеш) и задан ключ провайдера — без платного запроса (план A)
    app.include_router(buildHealthRouter({"redis": _redisCheck(app)}))
    app.include_router(
        buildTranscribeRouter(
            providerName=config.provider,
            fakeAudio=fakeAudio if config.provider == "fake" else None,
        )
    )
    return app


# Примечание: провайдер и разбор аудио по ASR_PROVIDER; ключ проверен в настройках
def _provider(config: AsrSettings, http: httpx.AsyncClient) -> tuple[SpeechToText, AudioProbe]:
    if config.provider != "yandex" or config.yandexApiKey is None:
        return FakeSpeechToText(), FakeAudioProbe()
    stt = YandexSpeechKit(
        http,
        config.yandexApiKey,
        url=config.yandexUrl,
        folderId=config.yandexFolderId,
        attemptTimeoutS=config.providerTimeoutS,
        retries=config.providerRetries,
        deadlineS=config.deadlineS,
    )
    return stt, OggOpusProbe()


# Примечание: PING текущего клиента Redis; до старта lifespan — ошибка, то есть «не готов»
def _redisCheck(app: FastAPI) -> HealthCheck:
    # Примечание: клиент берётся из app.state в момент проверки
    async def probe() -> object:
        redis: Redis = app.state.redis
        return await redis.ping()

    return HealthCheck(probe=probe)
