"""openLlmBrain: fake без сети; gigachat — ход идёт в GigaChat с ключом и моделью из настроек."""

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from brain.factory import openLlmBrain
from brain.settings import LlmSettings
from contracts.agent_side import ToolResult, ToolSpec, TurnInput, UserSettings

TZ = "Europe/Moscow"


class NoTools:
    async def listTools(self) -> list[ToolSpec]:
        return []

    async def call(self, name: str, args: object) -> ToolResult:
        raise AssertionError(name)


@pytest.fixture(autouse=True)
def cleanEnv(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in LlmSettings.envNames():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SERVICE_TOKEN", "s" * 32)


def turnInput() -> TurnInput:
    return TurnInput(
        text="привет",
        inputType="text",
        now=datetime(2026, 10, 24, 12, 0, tzinfo=ZoneInfo(TZ)),
        tz=TZ,
        settings=UserSettings(tz=TZ),
    )


# Примечание: fake — сети нет, у фейка нет ответов: ход — llm_bad_output, а не исключение
async def test_fakeProvider() -> None:
    async with openLlmBrain(LlmSettings()) as brain:
        result = await brain.run(turnInput(), NoTools())
    assert (result.errorCode, result.usage.model) == ("llm_bad_output", "fake")


# Примечание: gigachat — токен по ключу из настроек, вызов основной модели, ответ текстом
@respx.mock
async def test_gigachatProvider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "gigachat")
    monkeypatch.setenv("LLM_GIGACHAT_CREDENTIALS", "base64-key")
    settings = LlmSettings()
    expiresAtMs = int((time.time() + 1800) * 1000)
    auth = respx.post(settings.gigachatAuthUrl).mock(
        return_value=httpx.Response(200, json={"access_token": "tok", "expires_at": expiresAtMs})
    )
    message = {"role": "assistant", "content": "Привет! Чем помочь?"}
    chat = respx.post(f"{settings.gigachatApiUrl}/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": message}]})
    )
    async with openLlmBrain(settings) as brain:
        result = await brain.run(turnInput(), NoTools())
    assert (result.outcome, result.say) == ("answer", "Привет! Чем помочь?")
    assert auth.calls.last.request.headers["Authorization"] == "Basic base64-key"
    assert b'"model":"GigaChat-2-Max"' in chat.calls.last.request.content
