"""ВРЕМЕННАЯ MCP-заглушка зоны C (S1). После мержа заменить на заглушку D, затем на services/mcp.

Streamable HTTP на /mcp, четыре обязательных инструмента + update_item и delete_item
с полем caller в схеме — как у D.
Ответы — из fixtures/ по формам плана: изменяющие → {status, item, conflicts, free_slots},
list → {items, range, truncated}, search → {results: [{item, score}], best_match_confident},
ошибка → isError + конверт {"error": {code, message, details}}.
Повтор с тем же (user_id, idempotency_key) возвращает тот же ответ. В лог — caller, без текста.
J8: POST /internal/v1/users/resolve — user_id по max_user_id (в памяти, с 17), под SERVICE_TOKEN;
POST /internal/v1/dialog-turns — ход в памяти по trace_id, повтор — 202 без дубля.
"""

import hmac
import json
import os
import re
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
# Поиск по началу слов: короткие предлоги не считаем, слово режем до 4 букв («звонок» ~ «позвонить»)
MIN_WORD: Final = 4
STEM: Final = 4
# Свободные окна при конфликте: в пределах дня, не больше трёх
FIRST_SLOT_HOUR: Final = 9
LAST_SLOT_HOUR: Final = 20
MAX_FREE_SLOTS: Final = 3
# id записи: целиком или первые 8 символов (как у D, pattern в tools.json)
ID_RE: Final = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})?$")
# напоминание о встрече по умолчанию (settings.remind_before_min у D)
REMIND_BEFORE_MIN: Final = 15
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


# Примечание: схемы инструментов из fixtures/tools.json — копия contracts/mcp/tools.json D из main
# (caller там уже есть, через $ref); если caller нет — добавляем, как у D
def loadTools() -> list[types.Tool]:
    tools: list[types.Tool] = []
    for spec in loadFixture("tools.json"):
        schema = dict(_asJson(spec["inputSchema"]))
        properties = _asJson(schema["properties"])
        if "caller" not in properties:
            schema["properties"] = {"caller": CALLER_SCHEMA, **properties}
            schema["required"] = ["caller", *_asList(schema.get("required", []))]
        tools.append(
            types.Tool(
                name=str(spec["name"]),
                description=str(spec.get("description") or ""),
                input_schema=schema,
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
        # созданное пользователем — видно в list и search, встречи участвуют в конфликтах
        self._created: dict[int, list[Json]] = {}
        # правки update_item / delete_item поверх фикстур и созданного: id → поля или None (удалено)
        self._edits: dict[int, dict[str, Json | None]] = {}
        # удалённые записи — для delete_item restore=true
        self._deleted: dict[int, dict[str, Json]] = {}

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
            result = self._dispatch(name, args, ZoneInfo(str(caller["tz"])), key, userId)
        except ToolFailureError as failure:
            log("tool_failed", tool=name, user_id=userId, error_code=failure.code)
            result = _failure(failure)
        if isinstance(key, str):
            self._replies[(userId, key)] = result
        return result

    # Примечание: инструмент по имени (набор и аргументы — как у D: contracts/mcp/tools.json в main)
    def _dispatch(
        self, name: str, args: Json, zone: ZoneInfo, key: object, userId: int
    ) -> types.CallToolResult:
        if name not in self._known:
            raise ToolFailureError("not_found", f"Неизвестный инструмент {name}")
        handlers = {
            "create_task": lambda: self._createTask(args, zone, key, userId),
            "create_event": lambda: self._createEvent(args, zone, key, userId),
            "list_items": lambda: self._list(args, zone, userId),
            "search_items": lambda: self._search(args, zone, userId),
            "update_item": lambda: self._update(args, zone, userId),
            "complete_item": lambda: self._complete(args, zone, userId),
            "delete_item": lambda: self._delete(args, zone, userId),
            "check_conflicts": lambda: self._checkConflicts(args, zone, userId),
        }
        return handlers[name]()

    # Примечание: create_task: задача — срок в starts_at (дата или момент), напоминание —
    # remind_at обязателен, starts_at = null (как Item у D)
    def _createTask(
        self, args: Json, zone: ZoneInfo, key: object, userId: int
    ) -> types.CallToolResult:
        kind = str(args.get("kind") or "task")
        if kind not in {"task", "reminder"}:
            raise ToolFailureError("validation_error", "kind: task или reminder", {"field": "kind"})
        remindAt = self._future(_optionalTime(args, "remind_at"), "remind_at")
        if kind == "reminder" and remindAt is None:
            raise ToolFailureError(
                "validation_error", "Нет времени напоминания", {"field": "remind_at"}
            )
        startsAt, allDay = _dateOrTime(args, "starts_at", zone)
        if not allDay:
            self._future(startsAt, "starts_at")
        item = self._newItem(args, zone, key, kind=kind)
        item.update(
            {
                "starts_at": _iso(startsAt, zone),
                "all_day": allDay,
                "remind_at": _iso(remindAt, zone),
            }
        )
        return self._store(item, userId)

    # Примечание: create_event: начало обязательно, конец — +60 мин; пересечение без
    # allow_conflict — status=conflict, что мешает и свободные окна (не ошибка, J3)
    def _createEvent(
        self, args: Json, zone: ZoneInfo, key: object, userId: int
    ) -> types.CallToolResult:
        starts = self._future(_optionalTime(args, "starts_at"), "starts_at")
        if starts is None:
            raise ToolFailureError("validation_error", "Нет времени начала", {"field": "starts_at"})
        ends = _optionalTime(args, "ends_at") or starts + timedelta(minutes=DEFAULT_EVENT_MIN)
        if ends <= starts:
            raise ToolFailureError("validation_error", "Конец раньше начала", {"field": "ends_at"})
        busy = [] if args.get("allow_conflict") else self._overlapping(starts, ends, zone, userId)
        if busy:
            return self._conflict(starts, ends, busy, zone, userId)
        before = args.get("remind_before_min")
        minutes = (
            before
            if isinstance(before, int) and not isinstance(before, bool)
            else REMIND_BEFORE_MIN
        )
        item = self._newItem(args, zone, key, kind="event")
        item.update(
            {
                "starts_at": _iso(starts, zone),
                "ends_at": _iso(ends, zone),
                "remind_at": _iso(starts - timedelta(minutes=minutes), zone),
                "participants": [str(p) for p in _asList(args.get("participants"))],
            }
        )
        return self._store(item, userId)

    # Примечание: update_item: новое время (у напоминания — remind_at), название; встреча
    # сохраняет длительность и проверяет пересечения (кроме себя), если нет allow_conflict
    def _update(self, args: Json, zone: ZoneInfo, userId: int) -> types.CallToolResult:
        item = self._find(args, zone, userId)
        patch: Json = {}
        if (title := str(args.get("title") or "").strip()) != "":
            patch["title"] = title
        remindAt = self._future(_optionalTime(args, "remind_at"), "remind_at")
        if remindAt is not None:
            patch["remind_at"] = _iso(remindAt, zone)
        starts = self._future(_optionalTime(args, "starts_at"), "starts_at")
        if starts is not None:
            patch["starts_at"] = _iso(starts, zone)
            patch["all_day"] = False
            if item["kind"] == "event":
                ends = _optionalTime(args, "ends_at") or starts + (_itemEnd(item) - _itemTime(item))
                patch["ends_at"] = _iso(ends, zone)
                busy = (
                    []
                    if args.get("allow_conflict")
                    else [
                        e
                        for e in self._overlapping(starts, ends, zone, userId)
                        if e["id"] != item["id"]
                    ]
                )
                if busy:
                    return self._conflict(starts, ends, busy, zone, userId)
        if not patch:
            raise ToolFailureError("validation_error", "Нечего менять", {"field": "starts_at"})
        updated = self._patch(item, patch, userId)
        data: Json = {"status": "updated", "item": updated, "conflicts": [], "free_slots": []}
        return _ok(f"Изменено: {_brief(updated)}", data)

    # Примечание: complete_item: done=true — выполнено, false — снова открыто; встречу не отмечают
    def _complete(self, args: Json, zone: ZoneInfo, userId: int) -> types.CallToolResult:
        item = self._find(args, zone, userId)
        if item["kind"] == "event":
            raise ToolFailureError("validation_error", "Встречу нельзя выполнить", {"field": "id"})
        done = args.get("done", True) is not False
        updated = self._patch(item, {"status": "done" if done else "open"}, userId)
        data: Json = {
            "status": "completed" if done else "reopened",
            "item": updated,
            "conflicts": [],
            "free_slots": [],
        }
        return _ok(f"{'Выполнено' if done else 'Открыто снова'}: {_brief(updated)}", data)

    # Примечание: delete_item: мягкое удаление; restore=true — вернуть; повтор по удалённой —
    # not_found (агент отвечает «Уже отменено»)
    def _delete(self, args: Json, zone: ZoneInfo, userId: int) -> types.CallToolResult:
        if args.get("restore") is True:
            item = self._find(args, zone, userId, deleted=True)
            self._edits.setdefault(userId, {}).pop(str(item["id"]), None)
            data: Json = {"status": "restored", "item": item, "conflicts": [], "free_slots": []}
            return _ok(f"Восстановлено: {_brief(item)}", data)
        item = self._find(args, zone, userId)
        self._deleted.setdefault(userId, {})[str(item["id"])] = item
        self._edits.setdefault(userId, {})[str(item["id"])] = None
        data = {"status": "deleted", "item": item, "conflicts": [], "free_slots": []}
        return _ok(f"Удалено: {_brief(item)}", data)

    # Примечание: check_conflicts: что пересекается с [starts_at, ends_at) и свободные окна
    def _checkConflicts(self, args: Json, zone: ZoneInfo, userId: int) -> types.CallToolResult:
        starts, ends = _requiredTime(args, "starts_at"), _requiredTime(args, "ends_at")
        exclude = str(args.get("exclude_id") or "")
        busy = [
            e
            for e in self._overlapping(starts, ends, zone, userId)
            if not exclude or not str(e["id"]).startswith(exclude)
        ]
        slots = self._freeSlots(starts, ends, zone, userId) if busy else []
        data: Json = {"conflicts": [_briefOf(e) for e in busy], "free_slots": slots}
        return _ok(f"Пересечений: {len(busy)}", data)

    # Примечание: запись пользователя по id целиком или первым 8 символам, если однозначно;
    # deleted=True — искать среди удалённых (для restore)
    def _find(self, args: Json, zone: ZoneInfo, userId: int, *, deleted: bool = False) -> Json:
        wanted = str(args.get("id") or "").lower()
        if not ID_RE.fullmatch(wanted):
            raise ToolFailureError("validation_error", "Нужен id записи", {"field": "id"})
        if deleted:
            pool = list(self._deleted.get(userId, {}).values())
        else:
            today = self._clock().astimezone(zone).date()
            pool = self._visible(self._materialize(today, zone), userId)
        found = [item for item in pool if str(item["id"]).startswith(wanted)]
        if len(found) != 1:
            raise ToolFailureError("not_found", "Запись не найдена", {"id": wanted})
        return found[0]

    # Примечание: правка поверх фикстуры или созданного: id → поля; updated_at — «сейчас»
    def _patch(self, item: Json, patch: Json, userId: int) -> Json:
        edits = self._edits.setdefault(userId, {})
        itemId = str(item["id"])
        merged: Json = {**(edits.get(itemId) or {}), **patch, "updated_at": self._nowIso(item)}
        edits[itemId] = merged
        return {**item, **merged}

    # Примечание: фикстуры + созданное пользователем с его правками; удалённые не видны
    def _visible(self, fixtures: list[Json], userId: int) -> list[Json]:
        edits = self._edits.get(userId, {})
        items: list[Json] = []
        for item in [*fixtures, *self._created.get(userId, [])]:
            itemId = str(item["id"])
            if itemId in edits and edits[itemId] is None:
                continue
            items.append({**item, **(edits.get(itemId) or {})})
        return items

    # Примечание: новая запись в форме Item у D; id детерминирован от ключа идемпотентности
    def _newItem(self, args: Json, zone: ZoneInfo, key: object, *, kind: str) -> Json:
        title = str(args.get("title") or "").strip()
        if not title:
            raise ToolFailureError("validation_error", "Пустое название", {"field": "title"})
        now = self._clock().astimezone(zone).isoformat()
        itemId = uuid.uuid5(uuid.NAMESPACE_URL, f"va-stub:{key}") if key else uuid.uuid4()
        return {
            "id": str(itemId),
            "kind": kind,
            "title": title,
            "notes": args.get("notes"),
            "starts_at": None,
            "ends_at": None,
            "all_day": False,
            "remind_at": None,
            "status": "open",
            "rrule": args.get("rrule"),
            "participants": [],
            "source_text": args.get("source_text"),
            "created_at": now,
            "updated_at": now,
        }

    # Примечание: сохранить созданное и ответить как D: status=created
    def _store(self, item: Json, userId: int) -> types.CallToolResult:
        self._created.setdefault(userId, []).append(item)
        data: Json = {"status": "created", "item": item, "conflicts": [], "free_slots": []}
        return _ok(f"Создано: {_brief(item)}", data)

    # Примечание: время в прошлом — past_time (правило толкования G1 — у D)
    def _future(self, moment: datetime | None, field: str) -> datetime | None:
        if moment is not None and moment < self._clock():
            raise ToolFailureError("past_time", "Время уже прошло", {field: moment.isoformat()})
        return moment

    # Примечание: «сейчас» в поясе записи для updated_at
    def _nowIso(self, item: Json) -> str:
        raw = item.get("created_at")
        offset = datetime.fromisoformat(str(raw)).tzinfo if raw else UTC
        return self._clock().astimezone(offset).isoformat()

    # Примечание: встречи пользователя, пересекающиеся с [start, end)
    def _overlapping(
        self, start: datetime, end: datetime, zone: ZoneInfo, userId: int
    ) -> list[Json]:
        return [
            event
            for event in self._knownEvents(zone, userId)
            if _itemTime(event) < end and start < _itemEnd(event)
        ]

    # Примечание: встречи с концом: фикстуры от «сегодня» и созданные пользователем
    def _knownEvents(self, zone: ZoneInfo, userId: int) -> list[Json]:
        today = self._clock().astimezone(zone).date()
        items = self._visible(self._materialize(today, zone), userId)
        return [
            i
            for i in items
            if i["kind"] == "event" and i.get("starts_at") and i["status"] == "open"
        ]

    # Примечание: конфликт — не ошибка (J3): status=conflict, что мешает и до 3 свободных окон
    def _conflict(
        self, start: datetime, end: datetime, busy: list[Json], zone: ZoneInfo, userId: int
    ) -> types.CallToolResult:
        slots = self._freeSlots(start, end, zone, userId)
        conflicts = [_briefOf(item) for item in busy]
        data: Json = {
            "status": "conflict",
            "item": None,
            "conflicts": conflicts,
            "free_slots": slots,
        }
        lines = "; ".join(_brief(item) for item in busy)
        return _ok(f"Не создано: пересекается с {lines}; свободных окон: {len(slots)}", data)

    # Примечание: до 3 свободных окон той же длины в пределах 9–20 ч того же дня
    def _freeSlots(self, start: datetime, end: datetime, zone: ZoneInfo, userId: int) -> list[Json]:
        duration = end - start
        local = start.astimezone(zone)
        slots: list[Json] = []
        for hour in [
            *range(local.hour + 1, LAST_SLOT_HOUR + 1),
            *range(local.hour - 1, FIRST_SLOT_HOUR - 1, -1),
        ]:
            candidate = local.replace(hour=hour, minute=0)
            if candidate < self._clock() or self._overlapping(
                candidate, candidate + duration, zone, userId
            ):
                continue
            slots.append(
                {"starts_at": candidate.isoformat(), "ends_at": (candidate + duration).isoformat()}
            )
            if len(slots) == MAX_FREE_SLOTS:
                break
        return slots

    # Примечание: list_items: from и to — даты (J3 у D), [from, to); фикстуры раскладываются от
    # from; отбор по «когда» (starts_at или remind_at); status: open (по умолчанию), done, all
    def _list(self, args: Json, zone: ZoneInfo, userId: int) -> types.CallToolResult:
        first, last = _requiredDate(args, "from"), _requiredDate(args, "to")
        if last <= first:
            raise ToolFailureError("validation_error", "to не позже from", {"field": "to"})
        start = datetime(first.year, first.month, first.day, tzinfo=zone)
        end = datetime(last.year, last.month, last.day, tzinfo=zone)
        kinds = _asList(args.get("kind"))
        status = str(args.get("status") or "open")
        items = [
            item
            for item in self._visible(self._materialize(first, zone), userId)
            if _when(item) is not None
            and start <= _itemTime(item) < end
            and (not kinds or item["kind"] in kinds)
            and (status == "all" or item["status"] == status)
        ]
        items.sort(key=_itemTime)
        data: Json = {
            "items": items,
            "range": {"from": first.isoformat(), "to": last.isoformat()},
            "truncated": False,
        }
        lines = "; ".join(_brief(item) for item in items) or "записей нет"
        return _ok(f"Найдено {len(items)}: {lines}", data)

    # Примечание: search_items: совпадение по началу слов названия, от «сегодня» пользователя
    def _search(self, args: Json, zone: ZoneInfo, userId: int) -> types.CallToolResult:
        query = str(args.get("query") or "").strip().lower()
        if not query:
            raise ToolFailureError("validation_error", "Пустой запрос", {"field": "query"})
        limit = int(str(args.get("limit", 5)))
        includeDone = args.get("include_done", True) is not False
        stems = [word[:STEM] for word in query.split() if len(word) >= MIN_WORD]
        today = self._clock().astimezone(zone).date()
        found = [
            {"item": item, "score": 0.9}
            for item in self._visible(self._materialize(today, zone), userId)
            if any(stem in str(item["title"]).lower() for stem in stems)
            and (includeDone or item["status"] == "open")
        ][:limit]
        data: Json = {"results": found, "best_match_confident": bool(found)}
        lines = "; ".join(_brief(_asJson(r["item"])) for r in found) or "ничего не найдено"
        return _ok(f"Найдено {len(found)}: {lines}", data)

    # Примечание: фикстуры → записи в форме Item у D с настоящими датами: день = base +
    # day_offset; у напоминания время — в remind_at, у задачи без времени — весь день
    def _materialize(self, base: date, zone: ZoneInfo) -> list[Json]:
        items: list[Json] = []
        created = datetime(base.year, base.month, base.day, tzinfo=zone).isoformat()
        for raw in self._items:
            day = base + timedelta(days=int(str(raw["day_offset"])))
            clock = str(raw["time"]) if raw.get("time") else "00:00"
            hour, minute = (int(part) for part in clock.split(":"))
            moment = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
            item: Json = {
                "id": raw["id"],
                "kind": raw["kind"],
                "title": raw["title"],
                "notes": None,
                "starts_at": None,
                "ends_at": None,
                "all_day": bool(raw.get("all_day")),
                "remind_at": None,
                "status": raw.get("status", "open"),
                "rrule": None,
                "participants": [],
                "source_text": None,
                "created_at": created,
                "updated_at": created,
            }
            if raw["kind"] == "reminder":
                item["remind_at"] = moment.isoformat()
            else:
                item["starts_at"] = moment.isoformat()
            if raw.get("duration_min"):
                minutes = int(str(raw["duration_min"]))
                item["ends_at"] = (moment + timedelta(minutes=minutes)).isoformat()
                item["remind_at"] = (moment - timedelta(minutes=REMIND_BEFORE_MIN)).isoformat()
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


# Примечание: «когда» записи: начало встречи или срок задачи, у напоминания — remind_at
def _when(item: Json) -> str | None:
    raw = item.get("starts_at") or item.get("remind_at")
    return str(raw) if raw else None


# Примечание: краткая запись для conflicts (ItemBrief у D)
def _briefOf(item: Json) -> Json:
    keys = ("id", "kind", "title", "starts_at", "all_day", "status")
    return {key: item.get(key) for key in keys}


# Примечание: момент в поясе пользователя строкой ISO; None — None
def _iso(moment: datetime | None, zone: ZoneInfo) -> str | None:
    return moment.astimezone(zone).isoformat() if moment is not None else None


# Примечание: срок задачи: дата «2026-10-21» — весь день (полночь в поясе), иначе момент
def _dateOrTime(args: Json, field: str, zone: ZoneInfo) -> tuple[datetime | None, bool]:
    raw = args.get(field)
    if isinstance(raw, str) and len(raw) == len("2026-10-21"):
        try:
            day = date.fromisoformat(raw)
        except ValueError as exc:
            raise ToolFailureError(
                "validation_error", "Дата не ISO 8601", {"field": field}
            ) from exc
        return datetime(day.year, day.month, day.day, tzinfo=zone), True
    return _optionalTime(args, field), False


# Примечание: обязательная дата «YYYY-MM-DD» (from и to у list_items)
def _requiredDate(args: Json, field: str) -> date:
    raw = args.get(field)
    try:
        return date.fromisoformat(str(raw))
    except ValueError as exc:
        raise ToolFailureError(
            "validation_error", f"{field}: нужна дата", {"field": field}
        ) from exc


# Примечание: конец записи: ends_at или начало + 60 минут
def _itemEnd(item: Json) -> datetime:
    raw = item.get("ends_at")
    start = _itemTime(item)
    return datetime.fromisoformat(str(raw)) if raw else start + timedelta(minutes=DEFAULT_EVENT_MIN)


# Примечание: момент записи для отбора по периоду: starts_at, у напоминания — remind_at
def _itemTime(item: Json) -> datetime:
    return datetime.fromisoformat(str(_when(item)))


# Примечание: «Позвонить Ивану» чт 22.10 10:00 (id 7f3a9c2e) — как summary у D
def _brief(item: Json) -> str:
    title = f"«{item['title']}»"
    raw = _when(item)
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
    error: Json = {
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


# Примечание: история диалога J8 в памяти: trace_id → ход; повтор с тем же trace_id — не дубль
class StubDialogLog:
    # Примечание: ходы по trace_id (как уникальный ключ dialog_log у D)
    def __init__(self) -> None:
        self.turns: dict[str, Json] = {}

    # Примечание: True — записан впервые, False — повтор (тот же ответ 202)
    def store(self, traceId: str, body: Json) -> bool:
        if traceId in self.turns:
            return False
        self.turns[traceId] = body
        return True


# Примечание: POST /internal/v1/dialog-turns (J8): проверка формы, 202; тексты в лог не пишем
def buildDialogTurnsRoute(dialog: StubDialogLog) -> Route:
    # Примечание: user_id > 0, trace_id, две реплики user → assistant
    async def dialogTurns(request: Request) -> JSONResponse:
        if (denied := _authError(request)) is not None:
            return denied
        try:
            body = await request.json()
        except ValueError:
            return _errorResponse(request, 400, "validation_error", "тело не JSON")
        if not isinstance(body, dict) or not _validTurns(body):
            return _errorResponse(request, 400, "validation_error", "ход не по контракту J8")
        traceId = str(body["trace_id"])
        created = dialog.store(traceId, body)
        log(
            "dialog_turns_stored" if created else "dialog_turns_duplicate",
            user_id=body["user_id"],
            trace_id=traceId,
            latency_ms=body.get("latency_ms"),
        )
        return JSONResponse({"accepted": True, "duplicate": not created}, status_code=202)

    return Route("/internal/v1/dialog-turns", dialogTurns, methods=["POST"])


# Примечание: форма хода как DialogTurnsRequest у D (main: libs/contracts/internal.py)
def _validTurns(body: Json) -> bool:
    userId, traceId, turns = body.get("user_id"), body.get("trace_id"), body.get("turns")
    if not isinstance(userId, int) or isinstance(userId, bool) or userId <= 0:
        return False
    if not isinstance(traceId, str) or not traceId or not isinstance(turns, list):
        return False
    roles = [t.get("role") for t in turns if isinstance(t, dict)]
    # как у D: input_type — только voice | text (у реплики ассистента — нет)
    types_ = [t.get("input_type") for t in turns if isinstance(t, dict)]
    return roles == ["user", "assistant"] and types_[0] in {"voice", "text"}


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
            buildDialogTurnsRoute(StubDialogLog()),
        ],
    )
    log("stub_started", port=PORT)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_config=None, access_log=False)  # noqa: S104
    log("stub_stopped")


if __name__ == "__main__":
    main()
