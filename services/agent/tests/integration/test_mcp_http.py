"""McpToolExecutor по настоящему Streamable HTTP против временной MCP-заглушки (deploy/stub/mcp)."""

import asyncio
import json
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import fakeredis
import httpx
import pytest
import structlog
from pydantic import SecretStr

from agent.application.ports import DialogEntry, SettingsChange, UserInput
from agent.domain.user import Caller
from agent.infrastructure.http_dialog_log import HttpDialogLogWriter
from agent.infrastructure.http_user_resolver import HttpUserResolver
from agent.infrastructure.http_user_settings import HttpSettingsWriter
from agent.infrastructure.mcp_connection import McpConnection, httpSessionFactory
from agent.infrastructure.mcp_executor import McpReply, McpToolExecutor
from common.errors import AppError
from common.http import ServiceHttpClient
from common.lifecycle import managedTasks
from common.redis import KeyedRedis
from services.agent.tests.conftest import REPO_ROOT, SERVICE_TOKEN

pytestmark = pytest.mark.integration

STUB: Final = REPO_ROOT / "deploy" / "stub" / "mcp" / "server.py"
TZ: Final = "Asia/Yekaterinburg"
START_TIMEOUT_S: Final = 15.0


# Примечание: заглушка в подпроцессе; stdout — в файл, чтобы проверить, какой caller дошёл
class StubProcess:
    # Примечание: порт фиксируем — перезапуск должен подняться по тому же адресу
    def __init__(self, port: int, logPath: Path) -> None:
        self.port = port
        self.logPath = logPath
        self.process: subprocess.Popen[bytes] | None = None

    # Примечание: URL MCP-эндпоинта
    @property
    def url(self) -> str:
        return f"{self.root}/mcp"

    # Примечание: корень контейнера — служебный REST J8 и health
    @property
    def root(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    # Примечание: запуск и ожидание /health/live
    def start(self) -> None:
        with self.logPath.open("ab") as log:
            self.process = subprocess.Popen(  # noqa: S603 — свой скрипт из репо
                [sys.executable, str(STUB)],
                env={"STUB_PORT": str(self.port), "SERVICE_TOKEN": SERVICE_TOKEN, "PATH": ""},
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + START_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/health/live").status_code == 200:
                    return
            except httpx.TransportError:
                time.sleep(0.1)
        msg = f"MCP-заглушка не поднялась: {self.logPath.read_text()}"
        raise RuntimeError(msg)

    # Примечание: SIGTERM и ожидание выхода
    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            self.process.wait(timeout=10)
            self.process = None

    # Примечание: события JSON-лога заглушки
    def events(self, name: str) -> list[dict[str, object]]:
        lines = self.logPath.read_text(encoding="utf-8").splitlines()
        records = [json.loads(line) for line in lines if line.startswith("{")]
        return [record for record in records if record.get("event") == name]


# Примечание: свободный порт на loopback
def freePort() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


# Примечание: заглушка на время теста
@pytest.fixture
def stub(tmp_path: Path) -> Iterator[StubProcess]:
    process = StubProcess(freePort(), tmp_path / "stub.log")
    process.start()
    try:
        yield process
    finally:
        process.stop()


# Примечание: настоящая сессия по HTTP в реестре задач, как в lifespan агента
@asynccontextmanager
async def connection(url: str) -> AsyncIterator[McpConnection]:
    async with managedTasks(timeoutS=5) as tasks:
        mcp = McpConnection(httpSessionFactory(url, SecretStr(SERVICE_TOKEN)))
        mcp.start(tasks)
        try:
            yield mcp
        finally:
            await mcp.close()


# Примечание: caller хода с уникальным trace_id
def callerFor(uniqueId: str) -> Caller:
    return Caller(userId=int(uniqueId[:8], 16), tz=TZ, traceId=uniqueId)


# Примечание: схемы по HTTP — четыре обязательных инструмента и правка/удаление, caller вырезан
async def test_listToolsOverHttp(stub: StubProcess, uniqueId: str) -> None:
    async with connection(stub.url) as mcp:
        tools = await McpToolExecutor(mcp, callerFor(uniqueId)).listTools()
    assert {t.name for t in tools} == {
        "create_task",
        "create_event",
        "list_items",
        "search_items",
        "update_item",
        "complete_item",
        "delete_item",
        "check_conflicts",
    }
    for tool in tools:
        assert "caller" not in json.dumps(tool.inputSchema)


# Примечание: подложенный моделью user_id не доходит до MCP — заглушка видит caller агента
async def test_callerOverwrittenOverHttp(stub: StubProcess, uniqueId: str) -> None:
    caller = callerFor(uniqueId)
    due = (datetime.now(ZoneInfo(TZ)) + timedelta(days=1)).replace(microsecond=0)
    args: dict[str, object] = {
        "title": "Позвонить Ивану",
        "kind": "reminder",
        "remind_at": due.isoformat(),
        "caller": {"user_id": 1, "tz": "UTC", "trace_id": "0" * 32},
    }
    async with connection(stub.url) as mcp:
        result = await McpToolExecutor(mcp, caller).call("create_task", args)
    assert result.ok, result
    assert result.data is not None
    assert result.data["status"] == "created"
    assert "Позвонить Ивану" in result.summary
    [called] = stub.events("tool_called")
    assert called["user_id"] == caller.userId
    assert called["trace_id"] == uniqueId
    assert called["idempotency_key"] == f"{uniqueId}:1"
    assert called["tz"] == TZ


# Примечание: isError по HTTP → ok=False с кодом из конверта
async def test_pastTimeOverHttp(stub: StubProcess, uniqueId: str) -> None:
    past = (datetime.now(ZoneInfo(TZ)) - timedelta(hours=1)).isoformat()
    async with connection(stub.url) as mcp:
        result = await McpToolExecutor(mcp, callerFor(uniqueId)).call(
            "create_task", {"title": "Позвонить", "kind": "reminder", "remind_at": past}
        )
    assert not result.ok
    assert result.error is not None
    assert result.error.code == "past_time"


# Примечание: период [сегодня, завтра) в поясе теста — датами, как J3 у D
def todayArgs() -> dict[str, object]:
    today = datetime.now(ZoneInfo(TZ)).date()
    return {"from": today.isoformat(), "to": (today + timedelta(days=1)).isoformat()}


# Примечание: быстрый перезапуск MCP-сервера незаметен: следующий вызов проходит с первой попытки
async def test_serverRestart(stub: StubProcess, uniqueId: str) -> None:
    async with connection(stub.url) as mcp:
        tools = McpToolExecutor(mcp, callerFor(uniqueId))
        first = await tools.call("list_items", todayArgs())
        assert first.ok
        assert first.data is not None
        assert first.data["items"]
        stub.stop()
        stub.start()
        second = await tools.call("list_items", todayArgs())
    assert second.ok, second


# Примечание: MCP лежит во время вызова → сессия теряется, хозяин переподключается, повтор проходит
async def test_reconnectAfterOutage(stub: StubProcess, uniqueId: str) -> None:
    async with connection(stub.url) as mcp:
        tools = McpToolExecutor(mcp, callerFor(uniqueId), timeoutS=5)
        assert (await tools.call("list_items", todayArgs())).ok
        stub.stop()

        # Примечание: сервер возвращается через секунду, пока исполнитель ждёт
        async def comeBack() -> None:
            await asyncio.sleep(1.0)
            await asyncio.to_thread(stub.start)

        with structlog.testing.capture_logs() as logs:
            restarted = asyncio.create_task(comeBack())
            result = await tools.call("list_items", todayArgs())
            await restarted
    assert result.ok, result
    events = [e["event"] for e in logs]
    assert "mcp_connected" in events
    assert {"mcp_session_lost", "mcp_call_failed"} & set(events)
    keys = {e["idempotency_key"] for e in stub.events("tool_called")}
    assert f"{uniqueId}:2" in keys


# Примечание: J8 по HTTP: первый resolve создаёт пользователя, повтор — тот же user_id без created
async def test_resolveUserOverHttp(stub: StubProcess, uniqueId: str) -> None:
    maxUserId = int(uniqueId[:12], 16)
    async with ServiceHttpClient(
        baseUrl=stub.root, timeoutS=5, serviceToken=SecretStr(SERVICE_TOKEN), upstreamName="mcp"
    ) as http:
        first = await HttpUserResolver(http, KeyedRedis(fakeredis.FakeAsyncRedis())).resolve(
            maxUserId, 5, "Иван"
        )
        # Свежий кеш — второй вызов снова идёт в J8 и проверяет идемпотентность на стороне сервера
        again = await HttpUserResolver(http, KeyedRedis(fakeredis.FakeAsyncRedis())).resolve(
            maxUserId, 5, None
        )
    assert first.userId == again.userId
    assert first.tz == "Europe/Moscow"
    created = [e["created"] for e in stub.events("user_resolved")]
    assert created == [True, False]


# Примечание: S3 — PATCH настроек по HTTP: пояс сохраняется, resolve отдаёт сохранённое;
# незнакомый user_id — 404, пояс не IANA — 400 (у агента до заглушки не доходит)
async def test_settingsOverHttp(stub: StubProcess, uniqueId: str) -> None:
    maxUserId = int(uniqueId[:12], 16)
    async with ServiceHttpClient(
        baseUrl=stub.root, timeoutS=5, serviceToken=SecretStr(SERVICE_TOKEN), upstreamName="mcp"
    ) as http:
        cache = KeyedRedis(fakeredis.FakeAsyncRedis())
        user = await HttpUserResolver(http, cache).resolve(maxUserId, 5, None)
        change = SettingsChange(tz="Asia/Yekaterinburg")
        updated = await HttpSettingsWriter(http, cache).update(user, change)
        again = await HttpUserResolver(http, cache).resolve(maxUserId, 5, None)
        with pytest.raises(AppError) as missing:
            await http.request(
                "PATCH", "/internal/v1/users/999999999/settings", json={"tz": "Asia/Omsk"}
            )
        with pytest.raises(AppError) as badTz:
            await http.request(
                "PATCH", f"/internal/v1/users/{user.userId}/settings", json={"tz": "Mars/Base"}
            )
    assert updated.tz == "Asia/Yekaterinburg"
    assert (again.userId, again.tz) == (user.userId, "Asia/Yekaterinburg")
    assert (missing.value.code, badTz.value.code) == ("not_found", "validation_error")


# Примечание: J8 без сервисного токена — 401 unauthorized в конверте стандарта
async def test_resolveRequiresToken(stub: StubProcess) -> None:
    async with ServiceHttpClient(baseUrl=stub.root, timeoutS=5, upstreamName="mcp") as http:
        resolver = HttpUserResolver(http, KeyedRedis(fakeredis.FakeAsyncRedis()))
        with pytest.raises(AppError) as caught:
            await resolver.resolve(1, 1, None)
    assert caught.value.code == "unauthorized"


# Примечание: аргументы create_task с caller и заданным ключом идемпотентности
def reminderArgs(caller: Caller, key: str) -> dict[str, object]:
    due = (datetime.now(ZoneInfo(TZ)) + timedelta(days=1)).replace(microsecond=0)
    return {
        "title": "Позвонить",
        "kind": "reminder",
        "remind_at": due.isoformat(),
        "caller": {
            "user_id": caller.userId,
            "tz": caller.tz,
            "trace_id": caller.traceId,
            "idempotency_key": key,
        },
    }


# Примечание: id созданной записи из structuredContent
def itemId(reply: McpReply) -> str:
    assert reply.structured is not None
    item = reply.structured["item"]
    assert isinstance(item, dict)
    return str(item["id"])


# Примечание: повтор изменяющего вызова с тем же ключом не создаёт дубль — тот же id записи
async def test_sameKeyNoDuplicate(stub: StubProcess, uniqueId: str) -> None:
    caller = callerFor(uniqueId)
    async with connection(stub.url) as mcp:
        first = await mcp.callTool("create_task", reminderArgs(caller, f"{uniqueId}:1"), 5)
        again = await mcp.callTool("create_task", reminderArgs(caller, f"{uniqueId}:1"), 5)
        other = await mcp.callTool("create_task", reminderArgs(caller, f"{uniqueId}:2"), 5)
    assert itemId(first) == itemId(again) != itemId(other)
    assert len(stub.events("tool_replayed")) == 1


# Примечание: аргументы create_event с caller и своим ключом
def eventArgs(caller: Caller, key: str, startsAt: datetime, **extra: object) -> dict[str, object]:
    return {
        "title": "Встреча с Петром",
        "starts_at": startsAt.isoformat(),
        "caller": {
            "user_id": caller.userId,
            "tz": caller.tz,
            "trace_id": caller.traceId,
            "idempotency_key": key,
        },
        **extra,
    }


# Примечание: конфликт — не ошибка: status=conflict, что мешает, свободные окна;
# allow_conflict (как у D) — создаёт
async def test_eventConflict(stub: StubProcess, uniqueId: str) -> None:
    caller = callerFor(uniqueId)
    tomorrow = datetime.now(ZoneInfo(TZ)).replace(second=0, microsecond=0) + timedelta(days=1)
    dentist = tomorrow.replace(hour=9, minute=30)  # фикстура «Стоматолог», завтра 09:30–10:30
    async with connection(stub.url) as mcp:
        conflict = await mcp.callTool(
            "create_event", eventArgs(caller, f"{uniqueId}:1", dentist), 5
        )
        assert not conflict.isError
        data = conflict.structured
        assert data is not None
        assert data["status"] == "conflict"
        assert data["item"] is None
        titles = [c["title"] for c in data["conflicts"]]  # type: ignore[attr-defined]
        assert titles == ["Стоматолог"]
        slots = data["free_slots"]
        assert isinstance(slots, list)
        assert slots
        first = datetime.fromisoformat(slots[0]["starts_at"])
        created = await mcp.callTool("create_event", eventArgs(caller, f"{uniqueId}:2", first), 5)
        assert created.structured is not None
        assert created.structured["status"] == "created"
        again = await mcp.callTool("create_event", eventArgs(caller, f"{uniqueId}:3", first), 5)
        assert again.structured is not None
        assert again.structured["status"] == "conflict"
        forced = await mcp.callTool(
            "create_event", eventArgs(caller, f"{uniqueId}:4", first, allow_conflict=True), 5
        )
        assert forced.structured is not None
        assert forced.structured["status"] == "created"


# Примечание: созданное видно в поиске; «звонок Ивану» находит «Позвонить Ивану» (по началу слов)
async def test_searchFindsByStem(stub: StubProcess, uniqueId: str) -> None:
    caller = callerFor(uniqueId)
    async with connection(stub.url) as mcp:
        # ключ «:0» — исполнитель хода начнёт свои ключи с «:1», повтора не будет
        await mcp.callTool("create_task", reminderArgs(caller, f"{uniqueId}:0"), 5)
        reply = await McpToolExecutor(mcp, caller).call("search_items", {"query": "звонок Ивану"})
    assert reply.data is not None
    titles = [r["item"]["title"] for r in reply.data["results"]]  # type: ignore[attr-defined]
    assert "Позвонить Ивану" in titles
    assert "Позвонить" in titles


# Примечание: caller в аргументах сырого вызова — как его ставит исполнитель хода
def withCaller(caller: Caller, key: str, **args: object) -> dict[str, object]:
    return {
        **args,
        "caller": {
            "user_id": caller.userId,
            "tz": caller.tz,
            "trace_id": caller.traceId,
            "idempotency_key": key,
        },
    }


# Примечание: перенос (встреча сохраняет длительность) и удаление; повтор с тем же ключом —
# тот же ответ, новое нажатие «Отменить» — not_found; удалённое не видно в списке
async def test_rescheduleAndUndo(stub: StubProcess, uniqueId: str) -> None:
    caller = callerFor(uniqueId)
    start = datetime.now(ZoneInfo(TZ)).replace(hour=8, minute=0, second=0, microsecond=0)
    start += timedelta(days=2)
    async with connection(stub.url) as mcp:
        created = await mcp.callTool("create_event", eventArgs(caller, f"{uniqueId}:0", start), 5)
        eventId = itemId(created)
        moved = await mcp.callTool(
            "update_item",
            withCaller(
                caller,
                f"{uniqueId}:1",
                id=eventId[:8],
                starts_at=(start + timedelta(hours=10)).isoformat(),
            ),
            5,
        )
        assert moved.structured is not None
        item = moved.structured["item"]
        assert isinstance(item, dict)
        assert moved.structured["status"] == "updated"
        moments = [datetime.fromisoformat(str(item[k])) for k in ("starts_at", "ends_at")]
        assert moments == [start + timedelta(hours=10), start + timedelta(hours=11)]
        deleteArgs = withCaller(caller, f"{uniqueId}:2", id=eventId)
        deleted = await mcp.callTool("delete_item", deleteArgs, 5)
        replay = await mcp.callTool("delete_item", deleteArgs, 5)
        second = await mcp.callTool(
            "delete_item", withCaller(caller, f"{uniqueId}:3", id=eventId), 5
        )
        day = start.date()
        listed = await mcp.callTool(
            "list_items",
            withCaller(
                caller,
                f"{uniqueId}:4",
                **{"from": day.isoformat(), "to": (day + timedelta(days=1)).isoformat()},
            ),
            5,
        )
    assert deleted.structured is not None
    assert deleted.structured["status"] == "deleted"
    assert replay.structured == deleted.structured
    assert second.isError
    assert second.structured is not None
    assert second.structured["error"]["code"] == "not_found"  # type: ignore[index]
    assert listed.structured is not None
    assert eventId not in json.dumps(listed.structured)


# Примечание: история диалога J8 по HTTP: повтор того же хода (тот же trace_id) — 202 без дубля;
# без токена — 401
async def test_dialogTurnsNoDuplicate(stub: StubProcess, uniqueId: str) -> None:
    entry = DialogEntry(
        userId=17,
        traceId=uniqueId,
        said=UserInput("что у меня завтра", "text"),
        answer="Завтра: ничего не запланировано.",
        itemIds=(),
        latencyMs=12,
    )
    async with (
        managedTasks(timeoutS=5) as tasks,
        ServiceHttpClient(
            baseUrl=stub.root, timeoutS=5, serviceToken=SecretStr(SERVICE_TOKEN), upstreamName="mcp"
        ) as http,
    ):
        writer = HttpDialogLogWriter(http, tasks)
        writer.record(entry)
        writer.record(entry)
        await tasks.shutdown(timeoutS=5)
    stored, duplicates = stub.events("dialog_turns_stored"), stub.events("dialog_turns_duplicate")
    assert [e["trace_id"] for e in stored] == [uniqueId]
    assert len(duplicates) == 1
    async with httpx.AsyncClient() as client:
        anonymous = await client.post(f"{stub.root}/internal/v1/dialog-turns", json={})
    assert anonymous.status_code == 401
    assert "что у меня" not in stub.logPath.read_text()


# Примечание: формы D (main): у напоминания время в remind_at, starts_at = null; list_items по
# датам находит его по remind_at; update_item двигает remind_at; complete_item done=true/false;
# delete_item restore=true возвращает запись
async def test_reminderLifecycleInDShape(stub: StubProcess, uniqueId: str) -> None:
    caller = callerFor(uniqueId)
    zone = ZoneInfo(TZ)
    remindAt = datetime.now(zone).replace(hour=11, minute=0, second=0, microsecond=0)
    remindAt += timedelta(days=2)
    day = remindAt.date()
    period = {"from": day.isoformat(), "to": (day + timedelta(days=1)).isoformat()}
    async with connection(stub.url) as mcp:
        created = await mcp.callTool(
            "create_task",
            withCaller(
                caller,
                f"{uniqueId}:0",
                title="Позвонить маме",
                kind="reminder",
                remind_at=remindAt.isoformat(),
            ),
            5,
        )
        assert created.structured is not None
        item = created.structured["item"]
        assert isinstance(item, dict)
        assert (item["starts_at"], item["remind_at"]) == (None, remindAt.isoformat())
        itemId = str(item["id"])
        listed = await mcp.callTool("list_items", withCaller(caller, f"{uniqueId}:1", **period), 5)
        assert itemId in json.dumps(listed.structured)
        later = remindAt + timedelta(hours=1)
        moved = await mcp.callTool(
            "update_item",
            withCaller(caller, f"{uniqueId}:2", id=itemId[:8], remind_at=later.isoformat()),
            5,
        )
        assert moved.structured is not None
        assert moved.structured["item"]["remind_at"] == later.isoformat()  # type: ignore[index]
        done = await mcp.callTool(
            "complete_item", withCaller(caller, f"{uniqueId}:3", id=itemId), 5
        )
        assert done.structured is not None
        assert done.structured["status"] == "completed"
        assert done.structured["item"]["status"] == "done"  # type: ignore[index]
        twice = await mcp.callTool(
            "complete_item", withCaller(caller, f"{uniqueId}:31", id=itemId), 5
        )
        assert twice.structured is not None
        assert twice.structured["status"] == "unchanged"
        openOnly = await mcp.callTool(
            "list_items", withCaller(caller, f"{uniqueId}:4", **period), 5
        )
        assert itemId not in json.dumps(openOnly.structured)
        await mcp.callTool("delete_item", withCaller(caller, f"{uniqueId}:5", id=itemId), 5)
        restored = await mcp.callTool(
            "delete_item", withCaller(caller, f"{uniqueId}:6", id=itemId, restore=True), 5
        )
        assert restored.structured is not None
        assert restored.structured["status"] == "restored"


# Примечание: перенос встречи на занятое время — conflict (сама встреча не мешает);
# check_conflicts отдаёт то же без создания; кнопка в истории как у D — только voice|text
async def test_updateConflictAndDialogInputType(stub: StubProcess, uniqueId: str) -> None:
    caller = callerFor(uniqueId)
    base = datetime.now(ZoneInfo(TZ)).replace(hour=12, minute=0, second=0, microsecond=0)
    base += timedelta(days=3)
    async with connection(stub.url) as mcp:
        first = await mcp.callTool("create_event", eventArgs(caller, f"{uniqueId}:0", base), 5)
        second = await mcp.callTool(
            "create_event", eventArgs(caller, f"{uniqueId}:1", base + timedelta(hours=2)), 5
        )
        selfMove = await mcp.callTool(
            "update_item",
            withCaller(
                caller,
                f"{uniqueId}:2",
                id=itemId(first),
                starts_at=(base + timedelta(minutes=30)).isoformat(),
            ),
            5,
        )
        assert selfMove.structured is not None
        assert selfMove.structured["status"] == "updated"
        clash = await mcp.callTool(
            "update_item",
            withCaller(
                caller,
                f"{uniqueId}:3",
                id=itemId(second),
                starts_at=base.isoformat(),
            ),
            5,
        )
        assert clash.structured is not None
        assert clash.structured["status"] == "conflict"
        checked = await mcp.callTool(
            "check_conflicts",
            withCaller(
                caller,
                f"{uniqueId}:4",
                starts_at=base.isoformat(),
                ends_at=(base + timedelta(hours=1)).isoformat(),
            ),
            5,
        )
        assert checked.structured is not None
        assert checked.structured["conflicts"]
    turns = {
        "user_id": 17,
        "trace_id": uniqueId,
        "turns": [
            {"role": "user", "text": "Отменить", "input_type": "action"},
            {"role": "assistant", "text": "Отменено"},
        ],
    }
    async with httpx.AsyncClient() as client:
        rejected = await client.post(
            f"{stub.root}/internal/v1/dialog-turns",
            json=turns,
            headers={"Authorization": f"Bearer {SERVICE_TOKEN}"},
        )
    assert rejected.status_code == 400
