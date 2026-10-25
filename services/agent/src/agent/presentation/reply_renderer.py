"""ReplyRenderer на шаблонах: подтверждения и списки — из данных инструментов, не из текста модели.

Правило 3: «готово» строится из TurnResult.actions[].result.data — того, что реально сохранено.
Свободные ответы и вопросы — текстом модели (plain, без разметки). Названия экранируются
под markdown, текст — не длиннее 4000 символов (лимит MAX), хвост списка — «…и ещё N».
"""

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from agent.application.actions import (
    CANCEL,
    CHOICE_ARG,
    CONFIRM_CONFLICT,
    ENDS_AT_ARG,
    ITEM_ID_ARG,
    KIND_ARG,
    LIST_PERIOD,
    PERIOD_ARG,
    PICK_OPTION,
    RESCHEDULE,
    SET_TZ,
    STARTS_AT_ARG,
    TITLE_ARG,
    TZ_ARG,
    UNDO,
)
from agent.application.item_time import ItemKind
from agent.application.ports import Clock, Notice, Welcome
from agent.application.reschedule import RescheduleChoice, RescheduleOption
from agent.application.turn_rules import successfulMutations
from agent.domain.timezones import RU_TIMEZONES, ruTimezone, utcOffset
from agent.domain.user import UserContext
from agent.presentation.templates import ReplyTemplates
from common.logging import getLogger
from common.markdown import cutText, escapeMarkdown
from common.ru_dates import DateFormatter
from common.trace import shortTraceId
from contracts.agent_side import ExecutedAction, TurnResult
from contracts.reply import (
    BUTTON_TEXT_MAX_LENGTH,
    MAX_OPEN_APP_PER_ROW,
    REPLY_TEXT_MAX_LENGTH,
    ActionButton,
    OpenAppButton,
    Reply,
    ReplyButton,
    ReplyFormat,
    ReplyItem,
    ReplyState,
)
from contracts.start_param import StartParam

LIST_TOOL: Final = "list_items"
SEARCH_TOOL: Final = "search_items"
WEEK_DAYS: Final = 7
OPTIONS_PER_ROW: Final = 3
ELLIPSIS: Final = "…"
# у похожих результатов поиска — по кнопке на запись, не больше лимита open_app в ряду MAX
SEARCH_APP_BUTTONS: Final = MAX_OPEN_APP_PER_ROW
# /settings: кнопок поясов в ряду (12 поясов → 3 ряда; лимит MAX — 7)
TZ_PER_ROW: Final = 4

_log = getLogger(__name__)

type Rows = list[list[ReplyButton]]


# Примечание: всё, что нужно одному рендеру: шаблоны, пояс и «сегодня» пользователя
@dataclass(frozen=True)
class _Scope:
    zone: ZoneInfo
    today: date
    dates: DateFormatter
    traceId: str


# Примечание: Reply по итогу хода; часы — чтобы «Сегодня/Завтра» считались от «сейчас» пользователя
class TemplateReplyRenderer:
    # Примечание: шаблоны грузятся и проверяются при старте (loadTemplates)
    def __init__(self, templates: ReplyTemplates, clock: Clock) -> None:
        self._t = templates
        self._clock = clock

    # Примечание: голос — первой строкой «Распознал: …»: одно сообщение вместо двух
    def render(
        self, result: TurnResult, user: UserContext, traceId: str, *, heard: str | None = None
    ) -> Reply:
        reply = self._render(result, user, traceId)
        return reply if heard is None else self._withHeard(reply, heard)

    # Примечание: error — ошибка, clarify — вопрос, done — подтверждение, answer — список/текст
    def _render(self, result: TurnResult, user: UserContext, traceId: str) -> Reply:
        if result.outcome == "error":
            return self.failure(result.errorCode or "internal", traceId)
        scope = self._scope(user, traceId)
        if result.outcome == "clarify":
            return self._clarify(result, scope)
        if result.outcome == "done":
            return self._confirm(successfulMutations(result.actions), scope)
        if (listed := _lastSuccessful(result.actions, LIST_TOOL)) is not None:
            return self._list(listed, scope)
        if (searched := _lastSuccessful(result.actions, SEARCH_TOOL)) is not None:
            return self._search(searched, scope)
        return _plain(result.say or self._t.answer.fallback, ReplyState.ANSWER, traceId)

    # Примечание: «Распознал: «…»» над ответом; в markdown — с экранированием, в plain — как есть
    def _withHeard(self, reply: Reply, heard: str) -> Reply:
        markdown = reply.format is ReplyFormat.MARKDOWN
        text = escapeMarkdown(heard) if markdown else " ".join(heard.split())
        line = self._t.answer.recognized.format(text=text)
        return reply.model_copy(
            update={"text": _cut(f"{line}\n{reply.text}", REPLY_TEXT_MAX_LENGTH)}
        )

    # Примечание: короткий ответ действия без LLM по имени шаблона; «Не понял кнопку» — ошибка
    def notice(self, notice: Notice, traceId: str) -> Reply:
        n = self._t.notices
        texts = {
            Notice.PENDING_EXPIRED: n.pendingExpired,
            Notice.CANCELLED: n.cancelled,
            Notice.ALREADY_UNDONE: n.alreadyUndone,
            Notice.ALREADY_COMPLETED: n.alreadyCompleted,
            Notice.UNKNOWN_ACTION: n.unknownAction,
            Notice.LLM_DAILY_LIMIT: n.llmDailyLimit,
        }
        state = ReplyState.ERROR if notice is Notice.UNKNOWN_ACTION else ReplyState.ANSWER
        return _plain(texts[notice], state, traceId)

    # Примечание: «На когда перенести?»: по варианту в ряд (подписи длинные), «Отмена» и запись
    def rescheduleQuestion(
        self,
        itemId: str,
        kind: ItemKind | None,
        options: Sequence[RescheduleOption],
        user: UserContext,
        traceId: str,
    ) -> Reply:
        t = self._t.reschedule
        zone = ZoneInfo(user.tz)
        rows: Rows = []
        for option in options:
            local = option.startsAt.astimezone(zone)
            label = {
                RescheduleChoice.IN_HOUR: t.inHour,
                RescheduleChoice.SAME_TIME_TOMORROW: t.sameTimeTomorrow,
                RescheduleChoice.TOMORROW_MORNING: t.tomorrowMorning,
            }[option.choice].format(time=f"{local:%H:%M}".removeprefix("0"))
            args: dict[str, str | int | bool] = {
                ITEM_ID_ARG: itemId,
                STARTS_AT_ARG: local.isoformat(),
            }
            if kind is not None:
                args[KIND_ARG] = kind
            rows.append([ActionButton(text=label, action=RESCHEDULE, args=args)])
        # запись в календаре — рядом с «Отмена»; id не по J6 — openApp даст today и warning
        app = openApp(self._t.buttons.openItem, lambda: StartParam.forItem(uuid.UUID(itemId)))
        rows.append([ActionButton(text=self._t.buttons.cancel, action=CANCEL), app])
        return _plain(t.question, ReplyState.CLARIFY, traceId, buttons=rows)

    # Примечание: «Напомню завтра, чт 22 октября, 10:00 / Позвонить Ивану» — момент из remind_at
    # записи, которую вернул update_item; кнопка — запись в календаре
    def snoozed(self, result: TurnResult, user: UserContext, traceId: str) -> Reply:
        scope = self._scope(user, traceId)
        action = next((a for a in reversed(result.actions) if a.result.ok), None)
        item = _replyItem((action.result.data or {}).get("item")) if action else None
        if item is None or item.remindAt is None:
            return self.render(result, user, traceId)
        when = scope.dates.moment(item.remindAt.astimezone(scope.zone), allDay=False)
        text = "\n".join([self._t.snooze.done.format(when=when), escapeMarkdown(item.title)])
        app = openApp(self._t.buttons.openItem, lambda: StartParam.forItem(item.id))
        return _markdown(text, ReplyState.DONE, traceId, buttons=[[app]], items=[item])

    # Примечание: /settings: «Пояс: Москва (UTC+3)» и подсказка; кнопки поясов по 4 в ряд
    # (MAX — до 7), текущий отмечен
    def settings(self, user: UserContext, traceId: str) -> Reply:
        s = self._t.settings
        text = "\n".join([s.header, s.tz.format(tz=self._tzLabel(user.tz)), s.hint])
        zones: list[ReplyButton] = [
            ActionButton(
                text=s.current.format(city=zone.city) if zone.tz == user.tz else zone.city,
                action=SET_TZ,
                args={TZ_ARG: zone.tz},
            )
            for zone in RU_TIMEZONES
        ]
        rows: Rows = [zones[i : i + TZ_PER_ROW] for i in range(0, len(zones), TZ_PER_ROW)]
        return _plain(text, ReplyState.ANSWER, traceId, buttons=rows)

    # Примечание: подтверждение по сохранённому у D: «Пояс: Екатеринбург (UTC+5)»; после смены
    # пояса — кнопка «Сегодня», чтобы сразу увидеть день в новом поясе
    def settingsChanged(self, user: UserContext, traceId: str) -> Reply:
        s, b = self._t.settings, self._t.buttons
        text = s.tz.format(tz=self._tzLabel(user.tz))
        return _plain(text, ReplyState.DONE, traceId, buttons=_periodRow([("today", b.today)]))

    # Примечание: «Екатеринбург (UTC+5)»; пояс не из списка (задан у D иначе) — IANA как есть
    def _tzLabel(self, tz: str) -> str:
        zone = ruTimezone(tz)
        offset = utcOffset(tz, self._clock.now())
        return f"{zone.city if zone else tz} ({offset})"

    # Примечание: приветствие / справка: тексты агента, кнопки периода, «Мой день» (J6 today) и
    # «История» (J6 dialog)
    def welcome(self, kind: Welcome, traceId: str) -> Reply:
        w, b = self._t.welcome, self._t.buttons
        text = w.start if kind is Welcome.START else w.help
        rows = _periodRow([("today", b.today), ("tomorrow", b.tomorrow), ("week", b.week)])
        rows.append(
            [openApp(w.openToday, StartParam.today), openApp(w.openHistory, StartParam.dialog)]
        )
        return _plain(text, ReplyState.ANSWER, traceId, buttons=rows)

    # Примечание: лок пользователя занят другим ходом
    def busy(self, traceId: str) -> Reply:
        return _plain(self._t.answer.busy, ReplyState.ERROR, traceId)

    # Примечание: текст по коду ошибки + короткий код цепочки (первые 6 символов trace_id)
    def failure(self, errorCode: str, traceId: str) -> Reply:
        code = self._t.errors["code"].format(code=shortTraceId(traceId))
        return _plain(f"{self._t.errorText(errorCode)}\n{code}", ReplyState.ERROR, traceId)

    # Примечание: пояс и «сегодня» пользователя на момент рендера
    def _scope(self, user: UserContext, traceId: str) -> _Scope:
        zone = ZoneInfo(user.tz)
        today = self._clock.now().astimezone(zone).date()
        return _Scope(zone, today, DateFormatter(today), traceId)

    # Примечание: вопрос модели и варианты ответа кнопками (по 3 в ряд)
    def _clarify(self, result: TurnResult, scope: _Scope) -> Reply:
        options = result.clarify.options if result.clarify is not None else []
        buttons: list[ReplyButton] = [
            ActionButton(
                text=_cut(option, BUTTON_TEXT_MAX_LENGTH),
                action=PICK_OPTION,
                args={CHOICE_ARG: i},
            )
            for i, option in enumerate(options)
            if option.strip()
        ]
        rows: Rows = [
            buttons[i : i + OPTIONS_PER_ROW] for i in range(0, len(buttons), OPTIONS_PER_ROW)
        ]
        # пересечение встреч: «Всё равно создать» — по аргументам того create_event, что вернул
        # conflict (данные инструмента, а не pending Brain)
        if (force := self._forceButton(result.actions)) is not None:
            rows.append([force])
        # «Отмена» — всегда отдельным рядом: уточнение можно бросить, не отвечая
        rows.append([ActionButton(text=self._t.buttons.cancel, action=CANCEL)])
        text = result.say or self._t.answer.clarify
        return _plain(text, ReplyState.CLARIFY, scope.traceId, buttons=rows)

    # Примечание: кнопка confirm_conflict для последнего create_event или update_item
    # со status=conflict: создать встречу или перенести запись вопреки пересечению
    def _forceButton(self, actions: Sequence[ExecutedAction]) -> ActionButton | None:
        conflicted = next(
            (
                a
                for a in reversed(actions)
                if a.name in {"create_event", "update_item"}
                and a.result.ok
                and (a.result.data or {}).get("status") == "conflict"
            ),
            None,
        )
        if conflicted is None:
            return None
        startsAt = conflicted.args.get("starts_at")
        if not isinstance(startsAt, str):
            return None
        args: dict[str, str | int | bool] = {STARTS_AT_ARG: startsAt}
        if isinstance(endsAt := conflicted.args.get("ends_at"), str):
            args[ENDS_AT_ARG] = endsAt
        if conflicted.name == "update_item":
            itemId = conflicted.args.get("id")
            if not isinstance(itemId, str):
                return None
            args[ITEM_ID_ARG] = itemId
            return ActionButton(text=self._t.buttons.forceMove, action=CONFIRM_CONFLICT, args=args)
        title = conflicted.args.get("title")
        if not isinstance(title, str):
            return None
        args[TITLE_ARG] = title[:200]
        return ActionButton(text=self._t.buttons.forceCreate, action=CONFIRM_CONFLICT, args=args)

    # Примечание: подтверждение по каждому успешному изменению; кнопки — если запись одна
    def _confirm(self, mutations: Sequence[ExecutedAction], scope: _Scope) -> Reply:
        blocks: list[str] = []
        items: list[ReplyItem] = []
        statuses: list[str] = []
        for action in mutations:
            data = action.result.data or {}
            item = _replyItem(data.get("item"))
            blocks.append(self._confirmBlock(data, item, scope))
            if item is not None:
                items.append(item)
                statuses.append(str(data.get("status") or "created"))
        rows = self._itemButtons(items[0], statuses[0]) if len(items) == 1 else []
        text = _fit("\n\n".join(blocks), [], "")
        return _markdown(text, ReplyState.DONE, scope.traceId, buttons=rows, items=items)

    # Примечание: «Напоминание создано / Позвонить Ивану / Завтра, чт 22 октября, 10:00»
    def _confirmBlock(
        self, data: Mapping[str, object], item: ReplyItem | None, scope: _Scope
    ) -> str:
        status = str(data.get("status") or "created")
        if status == "conflict":
            return self._conflictBlock(data, scope)
        changed = self._t.changed
        header = {
            "updated": changed.updated,
            "completed": changed.completed,
            "deleted": changed.deleted,
        }.get(status)
        if item is None:
            return header or self._t.answer.fallback
        if header is None:
            created = self._t.created
            header = {"task": created.task, "reminder": created.reminder, "event": created.event}[
                item.kind
            ]
        return "\n".join([header, escapeMarkdown(item.title), self._when(item, scope)])

    # Примечание: пересечение встреч — не ошибка: что мешает и какие окна свободны
    def _conflictBlock(self, data: Mapping[str, object], scope: _Scope) -> str:
        lines = [self._t.changed.conflict]
        conflicts = [_replyItem(raw) for raw in _asList(data.get("conflicts"))]
        lines.extend(
            f"{self._when(item, scope)} {escapeMarkdown(item.title)}"
            for item in conflicts
            if item is not None
        )
        slots = [self._slot(raw, scope) for raw in _asList(data.get("free_slots"))]
        if slots := [slot for slot in slots if slot]:
            lines.append(self._t.changed.freeSlots.format(slots=", ".join(slots)))
        return "\n".join(lines)

    # Примечание: свободное окно {starts_at, ends_at} → «10:00–11:00»
    def _slot(self, raw: object, scope: _Scope) -> str:
        if not isinstance(raw, dict):
            return ""
        start, end = _moment(raw.get("starts_at"), scope), _moment(raw.get("ends_at"), scope)
        return scope.dates.timeSpan(start, end) if start is not None else ""

    # Примечание: «Отменить» — только у созданного (undo = удалить), «Изменить время» — у открытого;
    # у удалённой записи кнопок нет
    def _itemButtons(self, item: ReplyItem, status: str) -> Rows:
        if status == "deleted":
            return []
        b = self._t.buttons
        args: dict[str, str | int | bool] = {ITEM_ID_ARG: str(item.id)}
        actions: list[ReplyButton] = []
        if status == "created":
            actions.append(ActionButton(text=b.undo, action=UNDO, args=args))
        if item.status == "open":
            # вид записи — чтобы перенести нужное поле времени (у напоминания — remind_at)
            moveArgs: dict[str, str | int | bool] = {**args, KIND_ARG: item.kind}
            actions.append(ActionButton(text=b.reschedule, action=RESCHEDULE, args=moveArgs))
        app = openApp(b.openItem, lambda: StartParam.forItem(item.id))
        return [actions, [app]] if actions else [[app]]

    # Примечание: список за день, неделю (с понедельника) или период [from, to)
    def _list(self, action: ExecutedAction, scope: _Scope) -> Reply:
        data = action.result.data or {}
        period = _period(action.args, data, scope)
        if period is None:
            return _plain(
                action.result.summary or self._t.answer.fallback, ReplyState.ANSWER, scope.traceId
            )
        first, last = period
        items = sorted(
            (item for raw in _asList(data.get("items")) if (item := _replyItem(raw)) is not None),
            key=lambda item: (
                item.moment or datetime.combine(first, datetime.min.time(), scope.zone)
            ),
        )
        header, empty, rows = self._listFrame(first, last, scope)
        singleDay = first == last
        lines = [self._listLine(item, scope, withDay=not singleDay) for item in items]
        if not lines:
            return _markdown(empty, ReplyState.ANSWER, scope.traceId, buttons=rows)
        more = self._t.list.more
        text = _fit(header, lines, more)
        return _markdown(text, ReplyState.ANSWER, scope.traceId, buttons=rows, items=items)

    # Примечание: заголовок, текст «пусто» и кнопки — по виду периода
    def _listFrame(self, first: date, last: date, scope: _Scope) -> tuple[str, str, Rows]:
        t, b, dates = self._t.list, self._t.buttons, scope.dates
        tomorrow = scope.today + timedelta(days=1)
        isWeek = (last - first).days == WEEK_DAYS - 1 and first.weekday() == 0
        if first == last:
            day = dates.relativeDay(first)
            switch = [("today", b.today), ("tomorrow", b.tomorrow), ("week", b.week)]
            current = {scope.today: "today", tomorrow: "tomorrow"}.get(first)
            rows = _periodRow([s for s in switch if s[0] != current][:2])
            # «сегодня» — экран today, любой другой день — day_<дата> (J6); дата — в поясе
            # пользователя (first посчитан в scope.zone)
            dayParam = (
                StartParam.today if first == scope.today else (lambda: StartParam.forDay(first))
            )
            rows.append([openApp(b.openDay, dayParam)])
            return t.dayHeader.format(day=day), t.dayEmpty.format(day=day), rows
        if isWeek:
            span = dates.weekRange(first, last)
            rows = _periodRow([("today", b.today), ("tomorrow", b.tomorrow)])
            rows.append([openApp(b.openWeek, lambda: StartParam.forWeek(first))])
            return t.weekHeader.format(range=span), t.weekEmpty.format(range=span), rows
        bounds = {"first": dates.day(first), "last": dates.day(last)}
        rows = _periodRow([("today", b.today), ("week", b.week)])
        return t.rangeHeader.format(**bounds), t.rangeEmpty.format(**bounds), rows

    # Примечание: «10:00–11:00 Созвон»; за неделю — с днём: «чт 22 октября, 10:00 Созвон»
    def _listLine(self, item: ReplyItem, scope: _Scope, *, withDay: bool) -> str:
        title = escapeMarkdown(item.title)
        start = item.moment.astimezone(scope.zone) if item.moment else None
        if start is None or item.allDay:
            when = self._t.list.allDay
        else:
            end = item.endsAt.astimezone(scope.zone) if item.endsAt else None
            when = scope.dates.timeSpan(start, end)
        if withDay and start is not None:
            return self._t.list.lineWithDay.format(
                day=scope.dates.day(start.date()), when=when, title=title
            )
        return self._t.list.line.format(when=when, title=title)

    # Примечание: результаты поиска; несколько неуверенных — просим выбрать
    def _search(self, action: ExecutedAction, scope: _Scope) -> Reply:
        data = action.result.data or {}
        items = [
            item
            for raw in _asList(data.get("results"))
            if isinstance(raw, dict) and (item := _replyItem(raw.get("item"))) is not None
        ]
        t = self._t.search
        if not items:
            return _plain(t.empty, ReplyState.ANSWER, scope.traceId)
        confident = bool(data.get("best_match_confident")) or len(items) == 1
        lines = [
            t.line.format(when=self._when(item, scope), title=escapeMarkdown(item.title))
            for item in items
        ]
        text = _fit(t.found if confident else t.similar, lines, self._t.list.more)
        return _markdown(
            text, ReplyState.ANSWER, scope.traceId, buttons=self._searchButtons(items), items=items
        )

    # Примечание: одна запись — «В календаре»; несколько — по кнопке с названием на каждую из
    # первых трёх (лимит open_app в ряду MAX), остальные — в мини-приложении
    def _searchButtons(self, items: Sequence[ReplyItem]) -> Rows:
        if len(items) == 1:
            only = items[0]
            return [[openApp(self._t.buttons.openItem, lambda: StartParam.forItem(only.id))]]
        return [
            [
                openApp(_cut(item.title, BUTTON_TEXT_MAX_LENGTH), _itemParam(item))
                for item in items[:SEARCH_APP_BUTTONS]
            ]
        ]

    # Примечание: «Завтра, чт 22 октября, 10:00»; весь день — без времени; без даты — «без срока»
    def _when(self, item: ReplyItem, scope: _Scope) -> str:
        moment = item.moment
        if moment is None:
            return self._t.changed.noDate
        return scope.dates.moment(moment.astimezone(scope.zone), allDay=item.allDay)


# Примечание: последний успешный вызов инструмента с этим именем
def _lastSuccessful(actions: Sequence[ExecutedAction], name: str) -> ExecutedAction | None:
    return next((a for a in reversed(actions) if a.name == name and a.result.ok), None)


# Примечание: запись из данных инструмента; не по контракту (статус cancelled, нет id) — пропуск
def _replyItem(raw: object) -> ReplyItem | None:
    if not isinstance(raw, dict):
        return None
    try:
        return ReplyItem.fromWire(raw)
    except ValidationError:
        return None


# Примечание: период списка: из аргументов вызова, иначе из data.range; to не включается.
# У D (J3) from и to — даты; момент со смещением тоже понимаем (полночь в поясе пользователя)
def _period(
    args: Mapping[str, object], data: Mapping[str, object], scope: _Scope
) -> tuple[date, date] | None:
    span = data.get("range")
    fallback = span if isinstance(span, dict) else {}
    start = _boundary(args.get("from") or fallback.get("from"), scope)
    end = _boundary(args.get("to") or fallback.get("to"), scope)
    if start is None or end is None or end <= start:
        return None
    # [from, to): ровно полночь — последний день предыдущий; иначе день самого to
    lastMoment = end - timedelta(microseconds=1)
    return start.date(), lastMoment.date()


# Примечание: граница периода: дата «2026-10-21» → полночь в поясе пользователя; иначе момент
def _boundary(raw: object, scope: _Scope) -> datetime | None:
    if isinstance(raw, str) and len(raw) == len("2026-10-21"):
        try:
            day = date.fromisoformat(raw)
        except ValueError:
            return None
        return datetime.combine(day, datetime.min.time(), scope.zone)
    return _moment(raw, scope)


# Примечание: ISO-строка со смещением → момент в поясе пользователя; иначе None
def _moment(raw: object, scope: _Scope) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return value.astimezone(scope.zone) if value.utcoffset() is not None else None


# Примечание: ряд кнопок переключения периода (list_period без LLM)
def _periodRow(periods: Sequence[tuple[str, str]]) -> Rows:
    row: list[ReplyButton] = [
        ActionButton(text=text, action=LIST_PERIOD, args={PERIOD_ARG: period})
        for period, text in periods
    ]
    return [row] if row else []


# Примечание: заголовок + строки в лимит MAX; не влезло — последняя строка «…и ещё N»
def _fit(header: str, lines: Sequence[str], more: str) -> str:
    text = header
    for index, line in enumerate(lines):
        rest = len(lines) - index
        tail = f"\n{more.format(count=rest - 1)}" if rest > 1 and more else ""
        if len(text) + 1 + len(line) + len(tail) > REPLY_TEXT_MAX_LENGTH:
            return _cut(
                f"{text}\n{more.format(count=rest)}" if more else text, REPLY_TEXT_MAX_LENGTH
            )
        text = f"{text}\n{line}"
    return _cut(text, REPLY_TEXT_MAX_LENGTH)


# Примечание: обрезка до limit символов с многоточием; пару «\*» не рвёт (common.markdown)
def _cut(text: str, limit: int) -> str:
    return cutText(text, limit, ELLIPSIS)


# Примечание: кнопка мини-приложения только через валидатор J6: не собралась (дата или id не по
# J6) — в лог без значения и кнопка «сегодня», а не битый start_param у пользователя
def openApp(text: str, build: Callable[[], StartParam]) -> OpenAppButton:
    try:
        return OpenAppButton(text=text, startParam=str(build()))
    except (ValueError, ValidationError):
        _log.warning("start_param_invalid", fallback="today")
        return OpenAppButton(text=text, startParam=str(StartParam.today()))


# Примечание: start_param карточки записи — замыкание по конкретной записи
def _itemParam(item: ReplyItem) -> Callable[[], StartParam]:
    return lambda: StartParam.forItem(item.id)


# Примечание: list из JSON инструмента
def _asList(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


# Примечание: ответ текстом модели или фиксированным текстом — без разметки
def _plain(text: str, state: ReplyState, traceId: str, *, buttons: Rows | None = None) -> Reply:
    return Reply(
        text=_cut(text.strip() or ELLIPSIS, REPLY_TEXT_MAX_LENGTH),
        state=state,
        buttons=buttons or [],
        traceId=traceId,
    )


# Примечание: ответ по шаблону с экранированными названиями
def _markdown(
    text: str,
    state: ReplyState,
    traceId: str,
    *,
    buttons: Rows | None = None,
    items: Sequence[ReplyItem] = (),
) -> Reply:
    return Reply(
        text=text,
        format=ReplyFormat.MARKDOWN,
        state=state,
        buttons=buttons or [],
        items=list(items),
        traceId=traceId,
    )
