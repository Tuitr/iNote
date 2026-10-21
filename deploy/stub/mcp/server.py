"""ВРЕМЕННАЯ MCP-заглушка зоны C (S1). После мержа заменить на заглушку D, затем на services/mcp.

Streamable HTTP на /mcp, четыре обязательных инструмента с полем caller в схеме — как у D.
Ответы — из fixtures/ по формам плана: изменяющие → {status, item, conflicts, free_slots},
list → {items, range, truncated}, search → {results: [{item, score}], best_match_confident},
ошибка → isError + конверт {"error": {code, message, details}}.
Повтор с тем же (user_id, idempotency_key) возвращает тот же ответ. В лог — caller, без текста.
J8: POST /internal/v1/users/resolve — user_id по max_user_id (в памяти, с 17), под SERVICE_TOKEN.
"""

import hmac
import json
import os
import sys
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import mcp_types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

NAME: Final = "mcp"
OWNER: Final = "C (временно, вместо заглушки D)"
PORT: Final = int(os.environ.get("STUB_PORT", "8000"))
# Пустой токен — проверка выключена (запуск руками); в compose берётся из .env, как у всех
SERVICE_TOKEN: Final = os.environ.get("SERVICE_TOKEN", "")
FIRST_USER_ID: Final = 17
FIXTURES: Final = Path(__file__).with_name("fixtures")
WEEKDAYS: Final = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
DEFAULT_EVENT_MIN: Final = 60
# Поиск по началу слов: короткие предлоги не считаем, слово режем до 5 букв
MIN_WORD: Final = 3
# caller в схеме — как у D: агент вырезает его для LLM и заполняет сам (J3)
CALLER_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "properties": {
        "user_id": {"type": "integer"},
        "tz": {"type": "string"},
        "trace_id": {"type": "string"},
        "idempotency_key": {"type": "string"},
    },
    "required": ["user_id", "tz", "trace_id"],
}

type Json = dict[str, object]


# Примечание: ошибка инструмента в стандартном конверте → CallToolResult с isError
class ToolFailureError(Exception):
    # Примечание: code — из стандарта ошибок (validation_error, past_time, not_found…)
    def __init__(self, code: str, message: str, details: Json | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


# Примечание: одна строка JSON в stdout — формат как у LOG_FORMAT=json
def log(event: str, **fields: object) -> None:
    record = {"service": NAME, "event": event, "owner": OWNER, "stub": True, **fields}
    sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# Примечание: JSON из fixtures/
def loadFixture(name: str) -> list[Json]:
    data: list[Json] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


# Примечание: схемы инструментов из fixtures/tools.json + обязательный caller
def loadTools() -> list[types.Tool]:
    tools: list[types.Tool] = []
    for spec in loadFixture("tools.json"):
        schema = dict(_asJson(spec["inputSchema"]))
        schema["properties"] = {"caller": CALLER_SCHEMA, **_asJson(schema["properties"])}
        schema["required"] = ["caller", *_asList(schema.get("required", []))]
        tools.append(
            types.Tool(
                name=str(spec["name"]), description=str(spec["description"]), input_schema=schema
            )
        )
    return tools


# Примечание: состояние заглушки: схемы, записи-фикстуры и ответы по ключу идемпотентности
class StubTools:
    # Примечание: now — часы для тестов; по умолчанию системные
    def __init__(self, now: datetime | None = None) -> None:
        self.tools = loadTools()
        self._known = {tool.name for tool in self.tools}
        self._items = loadFixture("items.json")
        self._now = now
        self._replies: dict[tuple[int, str], types.CallToolResult] = {}

    # Примечание: вызов инструмента: проверка caller, ответ из фикстур, ошибка → isError
    def call(self, name: str, arguments: Mapping[str, object] | None) -> types.CallToolResult:
        args = dict(arguments or {})
        try:
            caller = _caller(args.pop("caller", None))
        except ToolFailureError as failure:
            log("tool_rejected", tool=name, error_code=failure.code)
            return _failure(failure)
        userId, key = int(str(caller["user_id"])), caller.get("idempotency_key")
        log(
            "tool_called",
            tool=name,
            user_id=userId,
            tz=caller["tz"],
            trace_id=caller["trace_id"],
            idempotency_key=key,
        )
        if isinstance(key, str) and (userId, key) in self._replies:
            log("tool_replayed", tool=name, user_id=userId, idempotency_key=key)
            return self._replies[(userId, key)]
        try:
            result = self._dispatch(name, args, ZoneInfo(str(caller["tz"])), key)
        except ToolFailureError as failure:
            log("tool_failed", tool=name, user_id=userId, error_code=failure.code)
            result = _failure(failure)
        if isinstance(key, str):
            self._replies[(userId, key)] = result
        return result

    # Примечание: инструмент по имени
    def _dispatch(self, name: str, args: Json, zone: ZoneInfo, key: object) -> types.CallToolResult:
        if name not in self._known:
            raise ToolFailureError("not_found", f"Неизвестный инструмент {name}")
        if name in {"create_task", "create_event"}:
            return self._create(name, args, zone, key)
        if name == "list_items":
            return self._list(args, zone)
        return self._search(args, zone)

    # Примечание: create_task / create_event: запись из аргументов, id детерминирован от ключа
    def _create(self, name: str, args: Json, zone: ZoneInfo, key: object) -> types.CallToolResult:
        title = str(args.get("title") or "").strip()
        if not title:
            raise ToolFailureError("validation_error", "Пустое название", {"field": "title"})
        isEvent = name == "create_event"
        whenField = "starts_at" if isEvent else "due_at"
        when = _optionalTime(args, whenField)
        if isEvent and when is None:
            raise ToolFailureError("validation_error", "Нет времени начала", {"field": "starts_at"})
        if when is not None and when < self._clock():
            raise ToolFailureError("past_time", "Время уже прошло", {whenField: when.isoformat()})
        itemId = uuid.uuid5(uuid.NAMESPACE_URL, f"va-stub:{key}") if key else uuid.uuid4()
        item: Json = {
            "id": str(itemId),
            "kind": "event" if isEvent else str(args.get("kind") or "task"),
            "title": title,
            "starts_at": when.astimezone(zone).isoformat() if when else None,
            "all_day": bool(args.get("all_day", when is None)),
            "status": "open",
        }
        if isEvent and when is not None:
            ends = _optionalTime(args, "ends_at") or when + timedelta(minutes=DEFAULT_EVENT_MIN)
            item["ends_at"] = ends.astimezone(zone).isoformat()
        data: Json = {"status": "created", "item": item, "conflicts": [], "free_slots": []}
        return _ok(f"Создано: {_brief(item)}", data)

    # Примечание: list_items: фикстуры раскладываются от даты from, отбор по [from, to)
    def _list(self, args: Json, zone: ZoneInfo) -> types.CallToolResult:
        start, end = _requiredTime(args, "from"), _requiredTime(args, "to")
        if end <= start:
            raise ToolFailureError("validation_error", "to раньше from", {"field": "to"})
        kinds = args.get("kinds")
        items = [
            item
            for item in self._materialize(start.astimezone(zone).date(), zone)
            if start <= _itemTime(item) < end and (not kinds or item["kind"] in _asList(kinds))
        ]
        data: Json = {
            "items": items,
            "range": {"from": start.isoformat(), "to": end.isoformat()},
            "truncated": False,
        }
        lines = "; ".join(_brief(item) for item in items) or "записей нет"
        return _ok(f"Найдено {len(items)}: {lines}", data)

    # Примечание: search_items: совпадение по началу слов названия, от «сегодня» пользователя
    def _search(self, args: Json, zone: ZoneInfo) -> types.CallToolResult:
        query = str(args.get("query") or "").strip().lower()
        if not query:
            raise ToolFailureError("validation_error", "Пустой запрос", {"field": "query"})
        limit = int(str(args.get("limit", 5)))
        stems = [word[:5] for word in query.split() if len(word) >= MIN_WORD]
        today = self._clock().astimezone(zone).date()
        found = [
            {"item": item, "score": 0.9}
            for item in self._materialize(today, zone)
            if any(stem in str(item["title"]).lower() for stem in stems)
        ][:limit]
        data: Json = {"results": found, "best_match_confident": bool(found)}
        lines = "; ".join(_brief(_asJson(r["item"])) for r in found) or "ничего не найдено"
        return _ok(f"Найдено {len(found)}: {lines}", data)

    # Примечание: фикстуры → записи с настоящими датами: день = base + day_offset
    def _materialize(self, base: date, zone: ZoneInfo) -> list[Json]:
        items: list[Json] = []
        for raw in self._items:
            day = base + timedelta(days=int(str(raw["day_offset"])))
            clock = str(raw["time"]) if raw.get("time") else "00:00"
            hour, minute = (int(part) for part in clock.split(":"))
            starts = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
            item: Json = {
                key: value for key, value in raw.items() if key not in {"day_offset", "time"}
            }
            item.pop("duration_min", None)
            item["starts_at"] = starts.isoformat()
            if raw.get("duration_min"):
                minutes = int(str(raw["duration_min"]))
                item["ends_at"] = (starts + timedelta(minutes=minutes)).isoformat()
            items.append(item)
        return items

    # Примечание: «сейчас» заглушки — фиксированное в тестах, иначе системное
    def _clock(self) -> datetime:
        return self._now or datetime.now(UTC)


# Примечание: caller обязателен: user_id, tz, trace_id; иначе validation_error
def _caller(raw: object) -> Json:
    if not isinstance(raw, dict):
        raise ToolFailureError("validation_error", "Нет caller", {"field": "caller"})
    caller: Json = raw
    missing = [f for f in ("user_id", "tz", "trace_id") if caller.get(f) in (None, "")]
    if missing or isinstance(caller.get("user_id"), bool):
        raise ToolFailureError("validation_error", "Неполный caller", {"missing": missing})
    try:
        ZoneInfo(str(caller["tz"]))
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ToolFailureError(
            "validation_error", "Неизвестный пояс", {"field": "caller.tz"}
        ) from exc
    return caller


# Примечание: время ISO 8601 строго со смещением (правило 2)
def _optionalTime(args: Json, field: str) -> datetime | None:
    raw = args.get(field)
    if raw is None:
        return None
    try:
        moment = datetime.fromisoformat(str(raw))
    except ValueError as exc:
        raise ToolFailureError("validation_error", "Время не ISO 8601", {"field": field}) from exc
    if moment.utcoffset() is None:
        raise ToolFailureError("validation_error", "Время без смещения", {"field": field})
    return moment


# Примечание: обязательное время
def _requiredTime(args: Json, field: str) -> datetime:
    moment = _optionalTime(args, field)
    if moment is None:
        raise ToolFailureError("validation_error", f"Нет поля {field}", {"field": field})
    return moment


# Примечание: момент записи для отбора по периоду
def _itemTime(item: Json) -> datetime:
    return datetime.fromisoformat(str(item["starts_at"]))


# Примечание: «Позвонить Ивану» чт 22.10 10:00 (id 7f3a9c2e) — как summary у D
def _brief(item: Json) -> str:
    title = f"«{item['title']}»"
    raw = item.get("starts_at")
    if not raw:
        return f"{title} (id {str(item['id'])[:8]})"
    moment = datetime.fromisoformat(str(raw))
    day = f"{WEEKDAYS[moment.weekday()]} {moment:%d.%m}"
    clock = "" if item.get("all_day") else f" {moment:%H:%M}"
    return f"{title} {day}{clock} (id {str(item['id'])[:8]})"


# Примечание: успешный ответ: текст для модели + structuredContent для агента
def _ok(summary: str, data: Json) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=summary)], structured_content=data
    )


# Примечание: ошибка: isError + конверт; текст для модели — код и сообщение
def _failure(failure: ToolFailureError) -> types.CallToolResult:
    envelope: Json = {
        "error": {"code": failure.code, "message": failure.message, "details": failure.details}
    }
    text = f"Ошибка {failure.code}: {failure.message}"
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        structured_content=envelope,
        is_error=True,
    )


# Примечание: dict из JSON фикстуры
def _asJson(value: object) -> Json:
    return value if isinstance(value, dict) else {}


# Примечание: list из JSON фикстуры
def _asList(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


# Примечание: MCP-сервер поверх StubTools (low-level API: схемы задаём сами, как в фикстурах)
def buildServer(stub: StubTools | None = None) -> Server[object]:
    tools = stub or StubTools()

    # Примечание: список инструментов — схемы с caller
    async def onListTools(
        ctx: object, params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools.tools)

    # Примечание: вызов инструмента по запросу клиента
    async def onCallTool(ctx: object, params: types.CallToolRequestParams) -> types.CallToolResult:
        return tools.call(params.name, params.arguments)

    return Server(NAME, version="stub-s1", on_list_tools=onListTools, on_call_tool=onCallTool)


# Примечание: пользователи J8 в памяти: max_user_id → user_id; пояс и настройки — по умолчанию
class StubUsers:
    # Примечание: id выдаются по порядку с 17 — как в contracts/examples/users
    def __init__(self) -> None:
        self._ids: dict[int, int] = {}

    # Примечание: идемпотентно по max_user_id: повтор возвращает того же пользователя
    def resolve(self, maxUserId: int) -> Json:
        created = maxUserId not in self._ids
        if created:
            self._ids[maxUserId] = FIRST_USER_ID + len(self._ids)
        return {
            "user_id": self._ids[maxUserId],
            "tz": "Europe/Moscow",
            "settings": {"default_event_min": DEFAULT_EVENT_MIN, "voice_replies": False},
            "created": created,
        }


# Примечание: конверт ошибки стандарта с trace_id запроса
def _errorResponse(request: Request, status: int, code: str, message: str) -> JSONResponse:
    error = {
        "code": code,
        "message": message,
        "details": {},
        "trace_id": request.headers.get("X-Trace-Id"),
    }
    return JSONResponse({"error": error}, status_code=status)


# Примечание: сервисный токен, как в middleware libs/common: нет — 401, чужой — 403
def _authError(request: Request) -> JSONResponse | None:
    if not SERVICE_TOKEN:
        return None
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return _errorResponse(request, 401, "unauthorized", "нужен сервисный токен")
    if not hmac.compare_digest(header.removeprefix("Bearer "), SERVICE_TOKEN):
        return _errorResponse(request, 403, "forbidden", "неверный сервисный токен")
    return None


# Примечание: POST /internal/v1/users/resolve (J8) поверх StubUsers
def buildResolveRoute(users: StubUsers) -> Route:
    # Примечание: разбор тела, проверка токена, ответ по contracts/examples/users
    async def resolveUser(request: Request) -> JSONResponse:
        if (denied := _authError(request)) is not None:
            return denied
        try:
            body = await request.json()
        except ValueError:
            return _errorResponse(request, 400, "validation_error", "тело не JSON")
        maxUserId = body.get("max_user_id") if isinstance(body, dict) else None
        if not isinstance(maxUserId, int) or isinstance(maxUserId, bool) or maxUserId <= 0:
            return _errorResponse(request, 400, "validation_error", "нужен max_user_id > 0")
        user = users.resolve(maxUserId)
        log(
            "user_resolved",
            user_id=user["user_id"],
            created=user["created"],
            trace_id=request.headers.get("X-Trace-Id"),
        )
        return JSONResponse(user)

    return Route("/internal/v1/users/resolve", resolveUser, methods=["POST"])


# Примечание: health для compose; ready = live — данных у заглушки нет
async def _health(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "checks": {}, "stub": NAME, "owner": OWNER})


# Примечание: точка входа: uvicorn на 0.0.0.0, Host mcp:8000 из compose разрешён
def main() -> None:
    app = buildServer().streamable_http_app(
        streamable_http_path="/mcp",
        host="0.0.0.0",  # noqa: S104 — порт внутри сети compose
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        custom_starlette_routes=[
            Route("/health/live", _health, methods=["GET"]),
            Route("/health/ready", _health, methods=["GET"]),
            buildResolveRoute(StubUsers()),
        ],
    )
    log("stub_started", port=PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_config=None, access_log=False)  # noqa: S104
    log("stub_stopped")


if __name__ == "__main__":
    main()
