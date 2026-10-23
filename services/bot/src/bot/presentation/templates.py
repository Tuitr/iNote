from collections.abc import Callable
from datetime import UTC, datetime
from typing import Final
from zoneinfo import ZoneInfo

from bot.application.ports import NotificationButtonTexts
from bot.domain.messages import fitToLimit
from bot.domain.voice import VoiceFailureReason
from common.markdown import escapeMarkdown
from common.ru_dates import DateFormatter
from contracts.notify import NotifyItem, NotifyRequest

# Тексты бота для пользователя — здесь, а не в use case (CLAUDE.md, «Стандарты»).
# Ответы по существу (подтверждения, списки, приветствие и справка) строит агент по своим шаблонам.
# Ошибки голосового бот отвечает сам, не доходя до агента
VOICE_NO_SPEECH: Final = "Не расслышал — повтори или напиши текстом.\nКод: {code}"
VOICE_TOO_LONG: Final = (
    "Слишком длинное голосовое — уложись в минуту или напиши текстом.\nКод: {code}"
)
VOICE_FAILED: Final = "Не получилось распознать голосовое — напиши текстом.\nКод: {code}"
UNKNOWN_COMMAND: Final = (
    "Не знаю команду /{name}. Есть /today, /tomorrow, /week, /settings и /help."
)
TOO_OFTEN: Final = "Слишком часто. Подожди минуту и попробуй снова."
AGENT_FAILED: Final = (
    "Не получилось связаться с ассистентом. Попробуй ещё раз через минуту.\nКод: {code}"
)
BUTTON_EXPIRED: Final = "Кнопка устарела — повтори запрос."
# сбой самого бота (например, Redis недоступен) — не тишина, а текст с кодом цепочки
BOT_FAILED: Final = "Что-то пошло не так. Попробуй ещё раз через минуту.\nКод: {code}"
# Уведомления J4 (Scheduler → бот): текст строит бот, Scheduler формата MAX не знает.
# Название экранируется под markdown, время — в поясе пользователя
NOTIFY_REMINDER: Final = "⏰ {title}\n{when}"
NOTIFY_EVENT: Final = "Скоро встреча: {title}\n{when}"
NOTIFY_TASK: Final = "Срок задачи: {title}\n{when}"
NOTIFY_NO_TIME: Final = "без срока"
NOTIFY_BUTTONS: Final = NotificationButtonTexts(
    done="Выполнено", snoozeHour="Отложить на 1 ч", tomorrow="Завтра", openItem="В календаре"
)
# Ответ на callback — всплывающее уведомление, длинное MAX обрежет
CALLBACK_NOTIFICATION_LIMIT: Final = 200
COMMAND_NAME_LIMIT: Final = 32


# Примечание: русские тексты бота (реализация порта BotTexts)
class RussianTexts:
    # Примечание: голосовое не распознано: свой текст на «не расслышал» и «слишком длинное»
    def voiceFailed(self, reason: VoiceFailureReason, code: str) -> str:
        if reason is VoiceFailureReason.NO_SPEECH:
            return VOICE_NO_SPEECH.format(code=code)
        if reason is VoiceFailureReason.TOO_LONG:
            return VOICE_TOO_LONG.format(code=code)
        return VOICE_FAILED.format(code=code)

    # Примечание: превышен лимит 20 сообщений в минуту
    def tooOften(self) -> str:
        return TOO_OFTEN

    # Примечание: агент не ответил или ответил ошибкой
    def agentFailed(self, code: str) -> str:
        return AGENT_FAILED.format(code=code)

    # Примечание: сбой самого бота
    def botFailed(self, code: str) -> str:
        return BOT_FAILED.format(code=code)

    # Примечание: ключа кнопки уже нет в Redis (24 ч) — уведомление на нажатие
    def buttonExpired(self) -> str:
        return BUTTON_EXPIRED[:CALLBACK_NOTIFICATION_LIMIT]

    # Примечание: имя команды обрезаем — это текст пользователя
    def unknownCommand(self, name: str) -> str:
        return UNKNOWN_COMMAND.format(name=name[:COMMAND_NAME_LIMIT])


# Примечание: тексты уведомлений J4 (порт NotificationTexts); now — часы для «Сегодня/Завтра»
class RussianNotificationTexts:
    # Примечание: по умолчанию системные часы; в тестах — замороженные
    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    # Примечание: «⏰ Позвонить Ивану / Сегодня, ср 21 октября, 10:00»; встреча — со временем
    # конца; срок задачи на весь день — без времени; ≤ 4000 символов
    def notificationBody(self, request: NotifyRequest) -> str:
        zone = ZoneInfo(request.user.tz)
        dates = DateFormatter(self._now().astimezone(zone).date())
        item = request.item
        title = escapeMarkdown(item.title)
        pattern = {"reminder": NOTIFY_REMINDER, "event": NOTIFY_EVENT}.get(
            request.kind, NOTIFY_TASK
        )
        return fitToLimit(pattern.format(title=title, when=_when(item, zone, dates)))

    # Примечание: подписи кнопок уведомления
    def notificationButtons(self) -> NotificationButtonTexts:
        return NOTIFY_BUTTONS


# Примечание: «когда» записи: встреча — день и 15:00–16:00, напоминание — remind_at
# (у D starts_at у напоминания пуст), задача — срок; нет времени — «без срока»
def _when(item: NotifyItem, zone: ZoneInfo, dates: DateFormatter) -> str:
    if item.kind == "event" and item.startsAt is not None:
        start = item.startsAt.astimezone(zone)
        end = item.endsAt.astimezone(zone) if item.endsAt else None
        day = dates.relativeDay(start.date())
        return f"{day}, {dates.timeSpan(start, end)}"
    moment = item.remindAt if item.kind == "reminder" else item.startsAt
    moment = moment or item.startsAt or item.remindAt
    if moment is None:
        return NOTIFY_NO_TIME
    return dates.moment(moment.astimezone(zone), allDay=item.allDay and item.kind == "task")
