"""J1 целиком: настоящий клиент бота (HttpAsrGateway) ↔ настоящий сервис ASR (фейковый провайдер).

Обе стороны стыка в одном тесте, без сети: бот ходит в приложение ASR через ASGI-транспорт.
Проверяем то, что бот читает: поля ответа, коды ошибок → причины голосового, токен, повторы.
"""

from collections.abc import AsyncIterator
from typing import Final

import fakeredis
import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr

from asr.infrastructure.settings import AsrSettings
from asr.main import createApp
from bot.domain.voice import AudioFile, VoiceFailureError, VoiceFailureReason
from bot.infrastructure.http_asr import HttpAsrGateway
from common.http import RetryPolicy, ServiceHttpClient

pytestmark = pytest.mark.contract

TOKEN: Final = "t" * 32


# Примечание: бот → ASR через ASGI; app ASR с запущенным lifespan
@pytest_asyncio.fixture
async def gateway(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[HttpAsrGateway]:
    for name in AsrSettings.envNames():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    redis = fakeredis.FakeAsyncRedis(decode_responses=True)
    app = createApp(redisFactory=lambda url: redis)
    async with app.router.lifespan_context(app):
        http = ServiceHttpClient(
            baseUrl="http://asr",
            timeoutS=15,
            serviceToken=SecretStr(TOKEN),
            retry=RetryPolicy(retries=2, initialDelayS=0, maxDelayS=0),
            upstreamName="asr",
            transport=httpx.ASGITransport(app=app),
        )
        try:
            yield HttpAsrGateway(http)
        finally:
            await http.aclose()


# Примечание: голосовое фейкового MAX («OggS» + текст)
def voice(text: str) -> AudioFile:
    return AudioFile(content=b"OggS" + text.encode(), mime="audio/ogg")


# Примечание: успех: бот получил текст (фейк — как есть) и флаги
async def test_botReadsTranscript(gateway: HttpAsrGateway) -> None:
    transcript = await gateway.transcribe(voice("напомни завтра в 10 позвонить ивану"))

    assert transcript.text == "напомни завтра в 10 позвонить ивану"
    assert transcript.lowConfidence is False
    assert transcript.durationMs > 0


# Примечание: неуверенное распознавание доходит до бота флагом (решает Brain)
async def test_botReadsLowConfidence(gateway: HttpAsrGateway) -> None:
    transcript = await gateway.transcribe(voice("~low~ перенеси на пять"))

    assert (transcript.text, transcript.lowConfidence) == ("перенеси на пять", True)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", VoiceFailureReason.NO_SPEECH),
        ("~long~ x", VoiceFailureReason.TOO_LONG),
        ("~down~ x", VoiceFailureReason.UNAVAILABLE),
    ],
)
# Примечание: коды ASR → причины, по которым бот выбирает текст пользователю
async def test_botMapsFailures(
    gateway: HttpAsrGateway, text: str, reason: VoiceFailureReason
) -> None:
    with pytest.raises(VoiceFailureError) as caught:
        await gateway.transcribe(voice(text))

    assert caught.value.reason is reason


# Примечание: не OGG — «не тот формат»
async def test_botMapsUnsupported(gateway: HttpAsrGateway) -> None:
    with pytest.raises(VoiceFailureError) as caught:
        await gateway.transcribe(AudioFile(content=b"RIFF....WAVE", mime="audio/wav"))

    assert caught.value.reason is VoiceFailureReason.UNSUPPORTED
