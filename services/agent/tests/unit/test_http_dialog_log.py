"""HttpDialogLogWriter (J8 dialog-turns): фон, ключ от trace_id, повторы, сбой — только лог."""

import asyncio
import json
import uuid
from typing import Final

import httpx
import respx
import structlog
from pydantic import SecretStr

from agent.application.ports import DialogEntry, UserInput
from agent.infrastructure.http_dialog_log import HttpDialogLogWriter
from common.http import RetryPolicy, ServiceHttpClient
from common.lifecycle import TaskRegistry
from common.trace import traceContext
from contracts.dialog_turns import DialogTurnsRequest

BASE: Final = "http://mcp:8000"
URL: Final = f"{BASE}/internal/v1/dialog-turns"
TOKEN: Final = "t" * 32
TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
ITEM: Final = uuid.UUID("7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90")
ENTRY: Final = DialogEntry(
    userId=17,
    traceId=TRACE,
    said=UserInput("напомни завтра в 10 позвонить Ивану", "voice"),
    answer="Напоминание создано",
    itemIds=(ITEM,),
    latencyMs=840,
)
ACCEPTED: Final = httpx.Response(202, json={"accepted": True})
DOWN: Final = httpx.Response(503, json={"error": {"code": "upstream_unavailable", "message": "x"}})


# Примечание: записать ход клиентом как в lifespan (2 повтора без пауз) и дождаться реестра
async def write(entry: DialogEntry = ENTRY) -> TaskRegistry:
    tasks = TaskRegistry()
    async with ServiceHttpClient(
        baseUrl=BASE,
        timeoutS=5,
        serviceToken=SecretStr(TOKEN),
        retry=RetryPolicy(retries=2, initialDelayS=0, maxDelayS=0),
        upstreamName="mcp",
    ) as http:
        with traceContext(TRACE):
            HttpDialogLogWriter(http, tasks).record(entry)
        await tasks.shutdown(timeoutS=5)
    return tasks


# Примечание: 202: тело по контракту J8, ключ идемпотентности от trace_id, токен и trace цепочки
@respx.mock
async def test_saved() -> None:
    route = respx.post(URL).mock(return_value=ACCEPTED)
    with structlog.testing.capture_logs() as logs:
        await write()
    request = route.calls.last.request
    body = DialogTurnsRequest.fromJson(request.content)
    assert (body.userId, body.traceId, body.latencyMs) == (17, TRACE, 840)
    user, assistant = body.turns
    assert (user.text, user.inputType) == (ENTRY.said.text, "voice")
    assert (assistant.text, assistant.itemIds) == ("Напоминание создано", [ITEM])
    assert request.headers["Idempotency-Key"] == f"dialog-turns:{TRACE}"
    assert request.headers["X-Trace-Id"] == TRACE
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert any(e["event"] == "dialog_turns_saved" for e in logs)
    assert "позвонить" not in str(logs).lower()


# Примечание: 503 дважды → третья попытка проходит; все попытки — тот же ключ и тот же trace_id
@respx.mock
async def test_retriedWithSameTrace() -> None:
    route = respx.post(URL).mock(side_effect=[DOWN, DOWN, ACCEPTED])
    await write()
    assert route.call_count == 3
    keys = {c.request.headers["Idempotency-Key"] for c in route.calls}
    traces = {json.loads(c.request.content)["trace_id"] for c in route.calls}
    assert (keys, traces) == ({f"dialog-turns:{TRACE}"}, {TRACE})


# Примечание: D недоступен после повторов — warning с trace_id и кодом, наружу ничего
@respx.mock
async def test_failureOnlyLogged() -> None:
    route = respx.post(URL).mock(return_value=DOWN)
    with structlog.testing.capture_logs() as logs:
        tasks = await write()
    assert route.call_count == 3
    [failed] = [e for e in logs if e["event"] == "dialog_turns_failed"]
    assert (failed["trace_id"], failed["error_code"], failed["log_level"]) == (
        TRACE,
        "upstream_unavailable",
        "warning",
    )
    assert tasks.activeCount == 0


# Примечание: record возвращается сразу — запрос ещё не отправлен, задача живёт в реестре
@respx.mock
async def test_recordDoesNotWait() -> None:
    gate = asyncio.Event()

    # Примечание: D «думает», пока тест не отпустит
    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return ACCEPTED

    route = respx.post(URL).mock(side_effect=slow)
    tasks = TaskRegistry()
    async with ServiceHttpClient(
        baseUrl=BASE, timeoutS=5, serviceToken=SecretStr(TOKEN), upstreamName="mcp"
    ) as http:
        HttpDialogLogWriter(http, tasks).record(ENTRY)
        assert tasks.activeCount == 1
        await asyncio.sleep(0)
        gate.set()
        report = await tasks.shutdown(timeoutS=5)
    assert (route.call_count, report.clean) == (1, True)


# Примечание: сервис уже останавливается — запись не принимается и логируется как потерянная
async def test_lostWhenShuttingDown() -> None:
    tasks = TaskRegistry()
    await tasks.shutdown()
    async with ServiceHttpClient(
        baseUrl=BASE, timeoutS=5, serviceToken=SecretStr(TOKEN), upstreamName="mcp"
    ) as http:
        with structlog.testing.capture_logs() as logs:
            HttpDialogLogWriter(http, tasks).record(ENTRY)
    assert [e["event"] for e in logs] == ["dialog_turns_lost"]
