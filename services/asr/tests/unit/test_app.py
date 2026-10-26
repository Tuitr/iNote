"""Приложение ASR целиком (фейковый провайдер, fakeredis): J1, токен, коды ошибок, health.

Ответ сверяется с моделью J1 и примером contracts/examples/asr — бот читает именно эту форму.
"""

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Final
from urllib.parse import quote

import fakeredis
import httpx
import pytest
import pytest_asyncio
import structlog
from fastapi import FastAPI
from redis.asyncio import Redis

from asr.infrastructure.settings import AsrSettings
from asr.main import createApp
from common.redis import KeyedRedis
from common.trace import newTraceId
from contracts.asr import TranscribeResponse
from services.asr.tests.oggdata import MONO_2500MS, fixture

TOKEN: Final = "t" * 32
AUTH: Final = {"Authorization": f"Bearer {TOKEN}"}
EXAMPLES: Final = Path(__file__).resolve().parents[4] / "contracts" / "examples" / "asr"


# Примечание: окружение ASR для тестов: фейковый провайдер
@pytest.fixture
def asrEnv(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in AsrSettings.envNames():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("ASR_PROVIDER", "fake")
    monkeypatch.setenv("LOG_FORMAT", "json")
    return monkeypatch


# Примечание: один fakeredis на тест — чтобы проверять ключи кеша
@pytest.fixture
def redis() -> Redis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


# Примечание: приложение с fakeredis
@pytest.fixture
def app(asrEnv: pytest.MonkeyPatch, redis: Redis) -> FastAPI:
    return createApp(redisFactory=lambda url: redis)


# Примечание: клиент с запущенным lifespan
@pytest_asyncio.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://asr") as http:
            yield http


# Примечание: POST /v1/transcribe так же, как его шлёт бот (HttpAsrGateway)
async def transcribe(
    client: httpx.AsyncClient,
    audio: bytes,
    *,
    headers: dict[str, str] | None = None,
    lang: str = "ru-RU",
) -> httpx.Response:
    return await client.post(
        "/v1/transcribe",
        files={"audio": ("voice.ogg", audio, "audio/ogg")},
        data={"mime": "audio/ogg", "lang": lang},
        headers={**AUTH, **(headers or {})},
    )


# Примечание: 200 — форма J1: ключи как в примере, модель бота разбирает ответ
async def test_transcribeMatchesContract(client: httpx.AsyncClient) -> None:
    response = await transcribe(client, b"OggS" + "что у меня завтра".encode())

    assert response.status_code == 200
    body = response.json()
    example = json.loads((EXAMPLES / "transcribe_response.json").read_text(encoding="utf-8"))
    assert set(body) == set(example)
    parsed = TranscribeResponse.fromWire(body)
    assert (parsed.text, parsed.lowConfidence) == ("что у меня завтра", False)
    assert parsed.durationMs > 0


# Примечание: ~low~ — неуверенно, порог считает ASR
async def test_lowConfidence(client: httpx.AsyncClient) -> None:
    body = (await transcribe(client, "OggS~low~ перенеси встречу".encode())).json()

    assert (body["text"], body["low_confidence"], body["confidence"]) == (
        "перенеси встречу",
        True,
        0.42,
    )


@pytest.mark.parametrize(
    ("audio", "status", "code"),
    [
        (b"OggS", 422, "no_speech"),
        (b"OggS   ", 422, "no_speech"),
        ("OggS~long~ очень длинное".encode(), 413, "audio_too_long"),
        ("OggS~down~ что угодно".encode(), 503, "upstream_unavailable"),
        (b"RIFF....WAVE", 400, "unsupported_format"),
        (b"", 400, "unsupported_format"),
    ],
    ids=["empty", "spaces", "long", "down", "wav", "no-bytes"],
)
# Примечание: отказ — конверт ошибки с кодом и статусом J1 и trace_id запроса
async def test_failuresAreEnvelopes(
    client: httpx.AsyncClient, audio: bytes, status: int, code: str
) -> None:
    traceId = newTraceId()

    response = await transcribe(client, audio, headers={"X-Trace-Id": traceId})

    assert response.status_code == status
    error = response.json()["error"]
    assert (error["code"], error["trace_id"]) == (code, traceId)
    assert response.headers["X-Trace-Id"] == traceId


# Примечание: голосовое больше ASR_MAX_BYTES — audio_too_long с лимитом в details
async def test_tooManyBytes(asrEnv: pytest.MonkeyPatch, redis: Redis) -> None:
    asrEnv.setenv("ASR_MAX_BYTES", "64")
    app = createApp(redisFactory=lambda url: redis)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://asr") as http:
            response = await transcribe(http, b"OggS" + b"a" * 100)

    assert response.status_code == 413
    assert response.json()["error"]["details"] == {"max_bytes": 64}


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        ({"Authorization": ""}, 401, "unauthorized"),
        ({"Authorization": "Bearer x"}, 403, "forbidden"),
    ],
)
# Примечание: /v1/transcribe — только с сервисным токеном
async def test_serviceToken(
    client: httpx.AsyncClient, headers: dict[str, str], status: int, code: str
) -> None:
    response = await transcribe(client, b"OggS" + b"hi", headers=headers)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code


# Примечание: язык — только вида ru-RU
async def test_badLang(client: httpx.AsyncClient) -> None:
    response = await transcribe(client, b"OggS" + b"hi", lang="russian")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


# Примечание: без файла — validation_error, а не 500
async def test_missingAudio(client: httpx.AsyncClient) -> None:
    response = await client.post("/v1/transcribe", data={"lang": "ru-RU"}, headers=AUTH)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


# Примечание: X-Fake-Text (URL-кодированный UTF-8) подменяет голосовое — только в фейковом режиме
async def test_fakeTextHeader(client: httpx.AsyncClient) -> None:
    response = await transcribe(
        client, fixture(MONO_2500MS), headers={"X-Fake-Text": quote("напомни через час")}
    )

    assert response.json()["text"] == "напомни через час"


# Примечание: повтор того же голосового — из кеша; ключ va:v1:asr:cache:{sha256} с TTL 7 дней
async def test_repeatIsCachedWithTtl(client: httpx.AsyncClient, redis: Redis) -> None:
    audio = b"OggS" + "покажи неделю".encode()

    first = await transcribe(client, audio)
    second = await transcribe(client, audio)

    assert first.json() == second.json()
    keys = [key async for key in redis.scan_iter("va:v1:asr:*")]
    assert len(keys) == 1
    assert keys[0].startswith("va:v1:asr:cache:")
    assert 0 < await redis.ttl(keys[0]) <= 7 * 24 * 3600


# Примечание: Redis упал — распознавание работает без кеша (Redis не источник правды)
async def test_worksWithoutRedis(asrEnv: pytest.MonkeyPatch) -> None:
    server = fakeredis.FakeServer()
    server.connected = False
    broken = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    app = createApp(redisFactory=lambda url: broken)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://asr") as http:
            response = await transcribe(http, b"OggS" + "что сегодня".encode())
            ready = await http.get("/health/ready")

    assert response.status_code == 200
    assert ready.status_code == 503


# Примечание: health: live всегда 200 и без токена; ready — Redis
async def test_health(client: httpx.AsyncClient) -> None:
    live = await client.get("/health/live")
    ready = await client.get("/health/ready")

    assert live.status_code == 200
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"redis": "ok"}


# Примечание: в логе запроса нет распознанного текста (правило 10)
async def test_noTextInLogs(client: httpx.AsyncClient) -> None:
    with structlog.testing.capture_logs() as logs:
        await transcribe(client, b"OggS" + "секретная встреча с врачом".encode())
        await transcribe(client, "OggS~long~ секретная длинная".encode())

    assert "секретная" not in json.dumps(logs, ensure_ascii=False)
    assert {"transcribed", "transcribe_failed"} <= {log["event"] for log in logs}


# Примечание: кеш пишет только итог — JSON модели J1, без аудио
async def test_cacheStoresOnlyTranscript(client: httpx.AsyncClient, redis: Redis) -> None:
    await transcribe(client, b"OggS" + "запиши задачу".encode())

    [key] = [key async for key in redis.scan_iter("va:v1:asr:*")]
    raw = await KeyedRedis(redis).client.get(key)
    assert raw is not None
    assert TranscribeResponse.fromJson(raw).text == "запиши задачу"
    assert "OggS" not in raw


# Примечание: сборка с SpeechKit: настоящий разбор OGG, запрос к провайдеру (MockTransport) с ключом
async def test_yandexWiring(asrEnv: pytest.MonkeyPatch, redis: Redis) -> None:
    asrEnv.setenv("ASR_PROVIDER", "yandex")
    asrEnv.setenv("ASR_YANDEX_API_KEY", "test-key")
    seen: list[httpx.Request] = []

    # Примечание: «SpeechKit»: запоминает запрос и отвечает строчным текстом
    def speechKit(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"result": "что у меня завтра"})

    app = createApp(
        redisFactory=lambda url: redis, providerTransport=httpx.MockTransport(speechKit)
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://asr") as http:
            ok = await transcribe(http, fixture(MONO_2500MS))
            fakeHeader = await transcribe(
                http, b"OggS" + b"x", headers={"X-Fake-Text": quote("подмена")}
            )

    assert ok.status_code == 200
    assert (ok.json()["text"], ok.json()["raw_text"], ok.json()["duration_ms"]) == (
        "Что у меня завтра",
        "что у меня завтра",
        2500,
    )
    assert seen[0].headers["Authorization"] == "Api-Key test-key"
    # вне фейкового режима X-Fake-Text не действует: «OggSx» — не OGG/Opus
    assert fakeHeader.status_code == 400
    assert len(seen) == 1
