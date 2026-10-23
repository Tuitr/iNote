"""POST /internal/v1/notify (J4) на приложении бота: токен, разбор, коды ответа, дедуп в Redis."""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Final

import fakeredis
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from redis.asyncio import Redis

from bot.infrastructure.settings import BotSettings
from bot.main import createApp
from services.bot.tests.fakes import EXAMPLES

TOKEN: Final = "t" * 32
AUTH: Final = {"Authorization": f"Bearer {TOKEN}"}
TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"
CHAT: Final = 500600700


# Примечание: одна fakeredis на приложение (как один Redis на стенде)
def fakeRedisFactory(url: str) -> Redis:
    return fakeredis.FakeAsyncRedis(decode_responses=True)


# Примечание: агент для readiness — health 200; в J4 агент не участвует
def agentHttp(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"status": "ok"})


# Примечание: бот с фейковым MAX; отправка «думает» 100 мс — чтобы запросы пересеклись
@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    monkeypatch.setenv("BOT_MAX_MODE", "fake")
    monkeypatch.setenv("BOT_POLL_TIMEOUT_S", "1")
    monkeypatch.setenv("BOT_FAKE_SEND_DELAY_MS", "100")
    return createApp(
        BotSettings(), redisFactory=fakeRedisFactory, agentTransport=httpx.MockTransport(agentHttp)
    )


# Примечание: клиент с запущенным lifespan
@pytest_asyncio.fixture
async def bot(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://bot") as client:
            yield client


# Примечание: тело запроса Scheduler'а из примера J4 с правками
def payload(**patch: object) -> dict[str, object]:
    raw: dict[str, object] = json.loads((EXAMPLES / "notify" / "request.json").read_text())
    return {**raw, **patch}


# Примечание: отправить уведомление, как Scheduler: токен и свой X-Trace-Id
async def notify(bot: httpx.AsyncClient, body: dict[str, object]) -> httpx.Response:
    return await bot.post("/internal/v1/notify", json=body, headers={**AUTH, "X-Trace-Id": TRACE})


# Примечание: доставка → 200 delivered с message_id; trace цепочки Scheduler'а сохраняется;
# повтор → duplicate без второго сообщения
async def test_deliveredThenDuplicate(bot: httpx.AsyncClient) -> None:
    first = await notify(bot, payload())
    assert first.status_code == 200
    assert first.headers["X-Trace-Id"] == TRACE
    body = first.json()
    assert (body["delivered"], body["duplicate"]) == (True, False)
    again = await notify(bot, payload(attempt=2))
    assert again.json() == {
        "delivered": True,
        "message_id": body["message_id"],
        "duplicate": True,
    }
    sent = (await bot.get("/dev/max/sent")).json()["messages"]
    assert [m["text"].split("\n")[0] for m in sent] == ["⏰ Позвонить Ивану"]


# Примечание: два одновременных запроса — одно сообщение; второй — 503 с Retry-After
async def test_parallelOneMessage(bot: httpx.AsyncClient) -> None:
    first, second = await asyncio.gather(notify(bot, payload()), notify(bot, payload(attempt=2)))
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 503]
    busy = first if first.status_code == 503 else second
    assert busy.headers["Retry-After"] == "5"
    assert busy.json()["error"]["details"] == {
        "notification_id": "5c1e7a90-3f2b-4d6c-8e1a-9b0c2d3e4f5a",
        "reason": "in_progress",
    }
    assert len((await bot.get("/dev/max/sent")).json()["messages"]) == 1


# Примечание: отказы MAX → коды J4: чат не найден — 404 chat_not_found, 429, 5xx — 503
@pytest.mark.parametrize(
    ("reason", "status", "code"),
    [
        ("chat_not_found", 404, "chat_not_found"),
        ("rate_limited", 429, "rate_limited"),
        ("unavailable", 503, "upstream_unavailable"),
    ],
)
async def test_maxFailures(bot: httpx.AsyncClient, reason: str, status: int, code: str) -> None:
    planned = await bot.post(
        "/dev/max/failures", json={"chat_id": CHAT, "reason": reason, "times": 1}
    )
    assert planned.status_code == 200, planned.text
    response = await notify(bot, payload())
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    retried = await notify(bot, payload(attempt=2))
    assert retried.status_code == 200


# Примечание: без токена — 401, с чужим — 403; MAX не трогаем
async def test_tokenRequired(bot: httpx.AsyncClient) -> None:
    assert (await bot.post("/internal/v1/notify", json=payload())).status_code == 401
    wrong = await bot.post(
        "/internal/v1/notify", json=payload(), headers={"Authorization": "Bearer nope"}
    )
    assert wrong.status_code == 403
    assert (await bot.get("/dev/max/sent")).json()["messages"] == []


# Примечание: время без смещения или attempt вне 1…5 — 400 validation_error
@pytest.mark.parametrize(
    "patch", [{"fire_at": "2026-10-18T10:00:00"}, {"attempt": 6}, {"kind": "memo"}]
)
async def test_invalidBody(bot: httpx.AsyncClient, patch: dict[str, object]) -> None:
    response = await notify(bot, payload(**patch))
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"


# Примечание: S4 (к заморозке) — каждый пример ответа J4 из contracts/examples/notify совпадает с
# тем, что отдаёт бот: статус, код и details (в details — notification_id, чтобы Scheduler
# связал ответ с записью; message — свободный текст, trace_id — свой у каждого запроса)
@pytest.mark.parametrize(
    ("example", "failure", "status"),
    [
        ("chat_not_found", "chat_not_found", 404),
        ("rate_limited", "rate_limited", 429),
        ("unavailable", "unavailable", 503),
    ],
)
async def test_errorExamplesMatch(
    bot: httpx.AsyncClient, example: str, failure: str, status: int
) -> None:
    plan = {"chat_id": CHAT, "reason": failure, "times": 1}
    assert (await bot.post("/dev/max/failures", json=plan)).status_code == 200
    response = await notify(bot, payload())
    expected = json.loads((EXAMPLES / "notify" / f"{example}.json").read_text())["error"]
    error = response.json()["error"]
    assert response.status_code == status
    assert (error["code"], error["details"]) == (expected["code"], expected["details"])


# Примечание: пример in_progress — параллельный повтор: 503, reason и notification_id, Retry-After
async def test_inProgressExampleMatches(bot: httpx.AsyncClient) -> None:
    first, second = await asyncio.gather(notify(bot, payload()), notify(bot, payload(attempt=2)))
    [busy] = [r for r in (first, second) if r.status_code == 503]
    expected = json.loads((EXAMPLES / "notify" / "in_progress.json").read_text())["error"]
    error = busy.json()["error"]
    assert (error["code"], error["details"]) == (expected["code"], expected["details"])
    assert busy.headers["Retry-After"] == "5"
