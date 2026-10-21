from collections.abc import AsyncIterator
from typing import Final

import httpx
import pytest
import pytest_asyncio
import respx
from pydantic import SecretStr

from common.errors import AppError
from common.http import RetryPolicy, ServiceHttpClient
from common.trace import isValidTraceId, traceContext

BASE: Final = "http://agent:8000"
TOKEN: Final = "s" * 32
TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
FAST_RETRY: Final = RetryPolicy(retries=2, initialDelayS=0.001, maxDelayS=0.002)


# Примечание: клиент с двумя повторами и мгновенной паузой
@pytest_asyncio.fixture
async def client() -> AsyncIterator[ServiceHttpClient]:
    async with ServiceHttpClient(
        baseUrl=BASE,
        timeoutS=1.0,
        serviceToken=SecretStr(TOKEN),
        retry=FAST_RETRY,
        upstreamName="agent",
    ) as c:
        yield c


# Примечание: каждый запрос несёт trace_id текущего контекста и сервисный токен
@respx.mock
async def test_sendsTraceAndToken(client: ServiceHttpClient) -> None:
    route = respx.get(f"{BASE}/health/live").respond(200, json={"status": "ok"})
    with traceContext(TRACE):
        await client.get("/health/live")
    request = route.calls.last.request
    assert request.headers["X-Trace-Id"] == TRACE
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"


# Примечание: вне цепочки клиент сам создаёт trace_id, а не шлёт пустой
@respx.mock
async def test_createsTraceWhenNone(client: ServiceHttpClient) -> None:
    route = respx.get(f"{BASE}/x").respond(200)
    await client.get("/x")
    assert isValidTraceId(route.calls.last.request.headers["X-Trace-Id"])


# Примечание: идемпотентный GET повторяется на 503 и в итоге успешен
@respx.mock
async def test_getRetriedOn503(client: ServiceHttpClient) -> None:
    route = respx.get(f"{BASE}/x").mock(
        side_effect=[httpx.Response(503), httpx.Response(503), httpx.Response(200, json={})]
    )
    response = await client.get("/x")
    assert response.status_code == 200
    assert route.call_count == 3


# Примечание: POST без ключа идемпотентности не повторяется (правило 6)
@respx.mock
async def test_postWithoutKeyNotRetried(client: ServiceHttpClient) -> None:
    route = respx.post(f"{BASE}/v1/messages").respond(503)
    with pytest.raises(AppError) as excInfo:
        await client.post("/v1/messages", json={"text": "привет"})
    assert route.call_count == 1
    assert excInfo.value.code == "upstream_unavailable"


# Примечание: POST с ключом повторяется, ключ один и тот же во всех попытках
@respx.mock
async def test_postWithKeyRetriedWithSameKey(client: ServiceHttpClient) -> None:
    route = respx.post(f"{BASE}/internal/v1/notify").mock(
        side_effect=[httpx.ConnectError("reset"), httpx.Response(200, json={"status": "delivered"})]
    )
    response = await client.post("/internal/v1/notify", json={}, idempotencyKey=f"{TRACE}:1")
    assert response.json() == {"status": "delivered"}
    keys = {call.request.headers["Idempotency-Key"] for call in route.calls}
    assert keys == {f"{TRACE}:1"}
    assert route.call_count == 2


# Примечание: 4xx не повторяется; конверт соседа превращается в AppError с его кодом
@respx.mock
async def test_errorEnvelopeBecomesAppError(client: ServiceHttpClient) -> None:
    envelope = {
        "error": {
            "code": "no_speech",
            "message": "речь не найдена",
            "details": {"duration_ms": 900},
            "trace_id": TRACE,
        }
    }
    route = respx.get(f"{BASE}/x").respond(422, json=envelope)
    with pytest.raises(AppError) as excInfo:
        await client.get("/x")
    error = excInfo.value
    assert route.call_count == 1
    assert (error.code, error.httpStatus) == ("no_speech", 422)
    assert error.details == {"duration_ms": 900, "upstream": "agent"}


# Примечание: после всех повторов таймаут → upstream_timeout 504
@respx.mock
async def test_timeoutAfterRetries(client: ServiceHttpClient) -> None:
    route = respx.get(f"{BASE}/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(AppError) as excInfo:
        await client.get("/slow")
    assert route.call_count == 3
    assert (excInfo.value.code, excInfo.value.httpStatus) == ("upstream_timeout", 504)


# Примечание: сосед недоступен → upstream_unavailable 503
@respx.mock
async def test_connectErrorIsUnavailable(client: ServiceHttpClient) -> None:
    respx.get(f"{BASE}/x").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(AppError) as excInfo:
        await client.get("/x")
    assert excInfo.value.code == "upstream_unavailable"


# Примечание: 5xx без конверта (прокси, падение) — тоже upstream_unavailable
@respx.mock
async def test_nonEnvelope5xx(client: ServiceHttpClient) -> None:
    respx.get(f"{BASE}/x").respond(502, text="Bad Gateway")
    with pytest.raises(AppError) as excInfo:
        await client.get("/x")
    assert excInfo.value.code == "upstream_unavailable"
    assert excInfo.value.details == {"upstream_status": 502}


# Примечание: retry=False отключает повторы даже для GET (health-пинг)
@respx.mock
async def test_retryDisabledPerCall(client: ServiceHttpClient) -> None:
    route = respx.get(f"{BASE}/health/live").respond(503)
    with pytest.raises(AppError):
        await client.get("/health/live", retry=False)
    assert route.call_count == 1


# Примечание: multipart: файл и поля формы уходят как есть; повтор с ключом шлёт то же аудио
async def test_multipartPostRetried() -> None:
    seen: list[httpx.Request] = []

    # Примечание: первый ответ 503, второй 200
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(503 if len(seen) == 1 else 200, json={})

    client = ServiceHttpClient(
        baseUrl="http://asr",
        timeoutS=1,
        retry=RetryPolicy(retries=1, initialDelayS=0, maxDelayS=0),
        transport=httpx.MockTransport(handle),
    )
    await client.post(
        "/v1/transcribe",
        files={"audio": ("voice.ogg", b"OggS-bytes", "audio/ogg")},
        data={"lang": "ru-RU"},
        idempotencyKey="k",
    )
    assert len(seen) == 2
    for request in seen:
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b"OggS-bytes" in request.content
        assert b'name="lang"\r\n\r\nru-RU' in request.content
