"""Шаблоны ответа: templates/ru.toml → проверенная модель; ошибка в файле — агент не стартует."""

import tomllib
from functools import cache
from importlib import resources
from typing import Final, Self, get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_snake

from contracts.agent_side import BrainErrorCode, ToolErrorCode

TEMPLATES_FILE: Final = "ru.toml"
WEEKDAYS: Final = 7
MONTHS: Final = 12
# Коды, для которых обязателен свой текст: всё, что может прийти в TurnResult.error_code
REQUIRED_ERROR_KEYS: Final = frozenset(
    {"code", "default", *get_args(BrainErrorCode.__value__), *get_args(ToolErrorCode.__value__)}
)


# Примечание: база секций: ключи TOML в snake_case, лишний ключ — ошибка (опечатка в шаблоне)
class _Section(BaseModel):
    model_config = ConfigDict(alias_generator=to_snake, extra="forbid", frozen=True)


# Примечание: дни недели, месяцы в родительном падеже и формы дат
class DateTemplates(_Section):
    weekdays: tuple[str, ...] = Field(min_length=WEEKDAYS, max_length=WEEKDAYS)
    months: tuple[str, ...] = Field(min_length=MONTHS, max_length=MONTHS)
    today: str
    tomorrow: str
    yesterday: str
    day: str
    dayWithYear: str
    relativeDay: str
    moment: str
    timeRange: str
    weekSameMonth: str
    weekTwoMonths: str


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


# Примечание: свободный ответ без текста модели и «ещё обрабатываю»
class AnswerTemplates(_Section):
    fallback: str
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


# Примечание: все шаблоны; errors — текст по коду ошибки, code — строка с коротким trace_id
class ReplyTemplates(_Section):
    dates: DateTemplates
    created: CreatedTemplates
    changed: ChangedTemplates
    list: ListTemplates
    search: SearchTemplates
    answer: AnswerTemplates
    buttons: ButtonTemplates
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
