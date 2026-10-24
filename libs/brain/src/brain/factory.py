"""Сборка LlmBrain из LlmSettings: клиент модели по LLM_PROVIDER и его HTTP-клиент."""

import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final

import httpx

from brain.gigachat import GigaChatClient, GigaChatConfig
from brain.llm import FakeLlmClient
from brain.llm_brain import LlmBrain
from brain.settings import LlmSettings

FAKE_MODEL: Final = "fake"


# Примечание: Brain на время жизни агента; HTTP-клиент GigaChat закрывается при остановке.
# fake — модель без сети с пустым сценарием: агент стартует, каждый ход — llm_bad_output.
# Ключ при gigachat гарантирует LlmSettings; проверка на None здесь — для типов
@asynccontextmanager
async def openLlmBrain(settings: LlmSettings) -> AsyncIterator[LlmBrain]:
    credentials = settings.gigachatCredentials
    if settings.provider == "fake" or credentials is None:
        yield LlmBrain(FakeLlmClient(script=[]), model=FAKE_MODEL, maxSteps=settings.maxSteps)
        return
    config = GigaChatConfig(
        credentials=credentials,
        scope=settings.gigachatScope,
        authUrl=settings.gigachatAuthUrl,
        apiUrl=settings.gigachatApiUrl,
        model=settings.model,
        fallbackModel=settings.fallbackModel,
    )
    async with httpx.AsyncClient(timeout=settings.timeoutS, verify=_verify(settings)) as http:
        yield LlmBrain(
            GigaChatClient(http, config), model=settings.model, maxSteps=settings.maxSteps
        )


# Примечание: сертификат GigaChat выпущен НУЦ Минцифры — его нет в системных, путь задают явно
def _verify(settings: LlmSettings) -> ssl.SSLContext | bool:
    if settings.gigachatCaBundle is None:
        return True
    return ssl.create_default_context(cafile=settings.gigachatCaBundle)
