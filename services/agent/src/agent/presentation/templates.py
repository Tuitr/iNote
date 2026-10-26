"""Шаблоны ответа: templates/ru.toml → проверенная модель; ошибка в файле — агент не стартует."""

import tomllib
from functools import cache
from importlib import resources
from typing import Final, Self, get_args

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_snake

from common.errors import ErrorCode
from contracts.agent_side import BrainErrorCode, ToolErrorCode

TEMPLATES_FILE: Final = "ru.toml"
# Коды, для которых обязателен свой текст: всё, что может прийти в TurnResult.error_code
# Коды стандарта (ErrorCode: в том числе unauthorized, forbidden, stale от J8 и MCP), Brain и
# инструментов — у каждого свой текст; без него агент не стартует
REQUIRED_ERROR_KEYS: Final = frozenset(
    {
        "default",
        *ErrorCode,
        *get_args(BrainErrorCode.__value__),
        *get_args(ToolErrorCode.__value__),
    }
)


# Примечание: база секций: ключи TOML в snake_case, лишний ключ — ошибка (опечатка в шаблоне)
class _Section(BaseModel):
    model_config = ConfigDict(alias_generator=to_snake, extra="forbid", frozen=True)


# Примечание: заголовки подтверждения по виду созданной записи
class CreatedTemplates(_Section):
    task: str
    reminder: str
    event: str


# Примечание: подтверждения изменений и конфликт встреч
class ChangedTemplates(_Section):
    updated: str
    completed: str
    deleted: str
    conflict: str
    freeSlots: str
    noDate: str
    remindBefore: str
    minutes: list[str]
    hours: list[str]
    days: list[str]


# Примечание: списки за день, неделю и произвольный период
class ListTemplates(_Section):
    dayHeader: str
    weekHeader: str
    rangeHeader: str
    dayEmpty: str
    weekEmpty: str
    rangeEmpty: str
    allDay: str
    line: str
    lineWithDay: str
    more: str


# Примечание: результаты поиска
class SearchTemplates(_Section):
    found: str
    similar: str
    empty: str
    line: str
    done: str


# Примечание: свободный ответ без текста модели и «ещё обрабатываю»
class AnswerTemplates(_Section):
    fallback: str
    recognized: str
    clarify: str
    busy: str


# Примечание: подписи кнопок
class ButtonTemplates(_Section):
    undo: str
    reschedule: str
    openItem: str
    openDay: str
    openWeek: str
    today: str
    tomorrow: str
    week: str
    cancel: str
    forceCreate: str
    forceMove: str


# Примечание: ответы действий без LLM: имена ключей совпадают с Notice (application/ports)
class NoticeTemplates(_Section):
    pendingExpired: str
    cancelled: str
    alreadyUndone: str
    alreadyCompleted: str
    unknownAction: str
    llmDailyLimit: str


# Примечание: ответ на «Отложить»: когда напомню снова
class SnoozeTemplates(_Section):
    done: str


# Примечание: вопрос «На когда перенести?» и подписи вариантов (RescheduleChoice)
class RescheduleTemplates(_Section):
    question: str
    inHour: str
    sameTimeTomorrow: str
    tomorrowMorning: str


# Примечание: приветствие на «Начать» и /start, справка на /help (раньше были в боте)
class WelcomeTemplates(_Section):
    start: str
    help: str
    openToday: str
    openHistory: str


# Примечание: /settings: экран и подтверждения; current — подпись текущего пояса на кнопке
class SettingsTemplates(_Section):
    header: str
    tz: str
    hint: str
    current: str


# Примечание: все шаблоны; errors — текст по коду ошибки
class ReplyTemplates(_Section):
    created: CreatedTemplates
    changed: ChangedTemplates
    list: ListTemplates
    search: SearchTemplates
    answer: AnswerTemplates
    buttons: ButtonTemplates
    notices: NoticeTemplates
    reschedule: RescheduleTemplates
    snooze: SnoozeTemplates
    welcome: WelcomeTemplates
    settings: SettingsTemplates
    errors: dict[str, str]

    # Примечание: у каждого возможного кода ошибки — свой текст, иначе пользователь увидит «default»
    @model_validator(mode="after")
    def _allErrorCodes(self) -> Self:
        missing = sorted(REQUIRED_ERROR_KEYS - self.errors.keys())
        if missing:
            msg = f"в шаблонах нет текстов ошибок: {', '.join(missing)}"
            raise ValueError(msg)
        return self

    # Примечание: текст ошибки по коду; незнакомый код — общий текст
    def errorText(self, code: str) -> str:
        return self.errors.get(code, self.errors["default"])


# Примечание: шаблоны из пакета (read-only, один раз на процесс)
@cache
def loadTemplates() -> ReplyTemplates:
    raw = resources.files(__package__).joinpath("templates", TEMPLATES_FILE).read_text("utf-8")
    return ReplyTemplates.model_validate(tomllib.loads(raw))
