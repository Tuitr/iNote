"""HttpAsrGateway по J1: multipart, ключ, повторы, ошибки → причины ответа бота."""

from typing import Final

import httpx
import pytest
import respx
from pydantic import SecretStr

from bot.domain.voice import AudioFile, Transcript, VoiceFailureError, VoiceFailureReason
from bot.infrastructure.http_asr import HttpAsrGateway
from common.http import RetryPolicy, ServiceHttpClient
from common.trace import traceContext

BASE: Final = "http://asr:8000"
TOKEN: Final = "t" * 32
TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
AUDIO: Final = AudioFile(content=b"OggS\x00voice", mime="audio/ogg")
OK: Final = {
    "text": "Напомни завтра в 10",
    "raw_text": "напомни завтра в десять",
    "confidence": 0.91,
    "low_confidence": False,
    "duration_ms": 1800,
}


# Примечание: распознать клиентом как в lifespan: 15 с, 2 повтора без пауз; клиент закрывается
async def transcribe(audio: AudioFile = AUDIO) -> Transcript:
    async with ServiceHttpClient(
        baseUrl=BASE,
        timeoutS=15,
        serviceToken=SecretStr(TOKEN),
        retry=RetryPolicy(retries=2, initialDelayS=0, maxDelayS=0),
        upstreamName="asr",
    ) as http:
        return await HttpAsrGateway(http).transcribe(audio)


# Примечание: конверт ошибки стандарта
def envelope(code: str) -> dict[str, object]:
    return {"error": {"code": code, "message": "x", "details": {}}}


# Примечание: успех: multipart с полями J1, токен, trace и ключ; ответ → Transcript
@respx.mock
async def test_transcribe() -> None:
    route = respx.post(f"{BASE}/v1/transcribe").mock(return_value=httpx.Response(200, json=OK))
    with traceContext(TRACE):
        transcript = await transcribe()
    assert (transcript.text, transcript.lowConfidence, transcript.durationMs) == (
        "Напомни завтра в 10",
        False,
        1800,
    )
    request = route.calls.last.request
    body = request.content
    assert b'name="audio"; filename="voice.ogg"' in body
    assert b"OggS\x00voice" in body
    assert b'name="mime"\r\n\r\naudio/ogg' in body
    assert b'name="lang"\r\n\r\nru-RU' in body
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["X-Trace-Id"] == TRACE
    assert request.headers["Idempotency-Key"].startswith("transcribe:")


# Примечание: коды J1 → причины; решаем по error.code, а не по статусу; 4xx не повторяем
@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        (422, "no_speech", VoiceFailureReason.NO_SPEECH),
        (413, "audio_too_long", VoiceFailureReason.TOO_LONG),
        (400, "unsupported_format", VoiceFailureReason.UNSUPPORTED),
        (400, "no_speech", VoiceFailureReason.NO_SPEECH),
        (400, "validation_error", VoiceFailureReason.UNAVAILABLE),
    ],
)
@respx.mock
async def test_errorCodes(status: int, code: str, reason: VoiceFailureReason) -> None:
    route = respx.post(f"{BASE}/v1/transcribe").mock(
        return_value=httpx.Response(status, json=envelope(code))
    )
    with pytest.raises(VoiceFailureError) as caught:
        await transcribe()
    assert caught.value.reason is reason
    assert caught.value.code == code
    assert route.call_count == 1


# Примечание: таймаут и 503 — 2 повтора с тем же ключом и телом, затем «недоступно»
@pytest.mark.parametrize(
    "respond",
    [httpx.ReadTimeout("slow"), httpx.Response(503, json=envelope("upstream_unavailable"))],
    ids=["timeout", "503"],
)
@respx.mock
async def test_retriesThenUnavailable(respond: httpx.Response | Exception) -> None:
    route = respx.post(f"{BASE}/v1/transcribe").mock(side_effect=[respond, respond, respond])
    with pytest.raises(VoiceFailureError) as caught:
        await transcribe()
    assert caught.value.reason is VoiceFailureReason.UNAVAILABLE
    assert route.call_count == 3
    keys = {call.request.headers["Idempotency-Key"] for call in route.calls}
    assert len(keys) == 1
    # Граница multipart у httpx случайная, поэтому сверяем само аудио в каждом теле
    assert all(b"OggS\x00voice" in call.request.content for call in route.calls)


# Примечание: первый ответ 503, второй — успех
@respx.mock
async def test_retryRecovers() -> None:
    respx.post(f"{BASE}/v1/transcribe").mock(
        side_effect=[
            httpx.Response(503, json=envelope("upstream_unavailable")),
            httpx.Response(200, json=OK),
        ]
    )
    assert (await transcribe()).confidence == 0.91


# Примечание: 200 не по контракту J1 — «недоступно», а не падение бота
@respx.mock
async def test_badBody() -> None:
    respx.post(f"{BASE}/v1/transcribe").mock(return_value=httpx.Response(200, json={"text": ""}))
    with pytest.raises(VoiceFailureError) as caught:
        await transcribe()
    assert caught.value.reason is VoiceFailureReason.UNAVAILABLE
