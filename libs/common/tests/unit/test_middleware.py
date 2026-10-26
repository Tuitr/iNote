from typing import Final

import httpx
import pytest
import structlog

from common.trace import isValidTraceId

VALID_TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"


# Примечание: без заголовка trace_id создаётся и возвращается в ответе
async def test_traceIdCreatedWhenAbsent(appClient: httpx.AsyncClient) -> None:
    response = await appClient.get("/ok")
    assert response.status_code == 200
    assert isValidTraceId(response.headers["X-Trace-Id"])


# Примечание: каноничный входящий trace_id сохраняется по всей цепочке
async def test_validTraceIdPropagated(appClient: httpx.AsyncClient) -> None:
    response = await appClient.get("/ok", headers={"X-Trace-Id": VALID_TRACE})
    assert response.headers["X-Trace-Id"] == VALID_TRACE


# Примечание: мусор в заголовке заменяется новым trace_id
async def test_invalidTraceIdReplaced(appClient: httpx.AsyncClient) -> None:
    response = await appClient.get("/ok", headers={"X-Trace-Id": "<script>"})
    traceId = response.headers["X-Trace-Id"]
    assert traceId != "<script>"
    assert isValidTraceId(traceId)


# Примечание: лог запроса с trace_id, статусом и duration_ms; без query-строки
async def test_requestLogged(appClient: httpx.AsyncClient) -> None:
    with structlog.testing.capture_logs() as logs:
        await appClient.get("/ok?text=секрет", headers={"X-Trace-Id": VALID_TRACE})
    entry = next(e for e in logs if e["event"] == "http_request")
    assert entry["path"] == "/ok"
    assert entry["status"] == 200
    assert entry["method"] == "GET"
    assert isinstance(entry["duration_ms"], int)
    assert "секрет" not in str(entry)


# Примечание: /internal/* без токена → 401 unauthorized
@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer ", "Bearer"])
async def test_internalWithoutTokenIs401(appClient: httpx.AsyncClient, header: str | None) -> None:
    headers = {} if header is None else {"Authorization": header}
    response = await appClient.get("/internal/v1/ping", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert isValidTraceId(response.headers["X-Trace-Id"])


# Примечание: неверный токен → 403 forbidden
async def test_internalWrongTokenIs403(appClient: httpx.AsyncClient) -> None:
    response = await appClient.get(
        "/internal/v1/ping", headers={"Authorization": "Bearer wrong-token"}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


# Примечание: верный токен пропускает; публичные пути токена не требуют
async def test_internalValidTokenPasses(
    appClient: httpx.AsyncClient, authHeader: dict[str, str]
) -> None:
    assert (await appClient.get("/internal/v1/ping", headers=authHeader)).status_code == 200
    assert (await appClient.get("/ok")).status_code == 200
    assert (await appClient.get("/internal", headers=authHeader)).status_code == 404
    assert (await appClient.get("/internal")).status_code == 401
