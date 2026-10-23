"""J2 · Agent Back (C) ↔ Brain (B): Python-интерфейс, не сеть. Черновик к G1.

Сторона C (владелец C): TurnInput, ToolExecutor и его типы — ToolSpec, ToolResult, ErrorInfo.
Сторона B — НАБРОСОК, ВЛАДЕЛЕЦ B (формат решает B): Clarify, ExecutedAction, Usage,
TurnResult, Brain. При мерже верх у этой части берёт B; сторона C к G4 — заморожена.

- Brain не видит user_id и не ходит в Redis и MCP сам — только через ToolExecutor (правило 1).
- pending — непрозрачный JSON: C хранит его в Redis (va:v1:agent:pending:{user_id}, 10 мин)
  и возвращает в следующем TurnInput, не разбирая.
- Подтверждение C строит из actions[].result.data, а не из текста модели (правило 3).
- Имена атрибутов — camelCase (предложение C к G0), на проводе и в JSON-примерах — snake_case.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Final, Literal, Protocol, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, field_validator, model_validator

from common.wire import WireModel

HISTORY_LIMIT: Final = 10
DEFAULT_EVENT_MIN: Final = 60

type JsonObject = dict[str, object]


# ===================== сторона C =====================


# Примечание: краткая запись (ItemBrief) — для фокуса, конфликтов и истории; у напоминания
# время — в remind_at (starts_at = null), как в Item у D (S3: поле добавлено)
class ItemBrief(WireModel):
    id: uuid.UUID
    kind: Literal["task", "reminder", "event"]
    title: str
    startsAt: AwareDatetime | None = None
    allDay: bool = False
    status: Literal["open", "done", "cancelled"] = "open"
    remindAt: AwareDatetime | None = None

    # Примечание: «когда» записи для людей: начало встречи или срока, у напоминания — remind_at
    @property
    def moment(self) -> datetime | None:
        return self.startsAt or self.remindAt


# Примечание: одна реплика истории диалога
class Turn(WireModel):
    role: Literal["user", "assistant"]
    text: str
    at: AwareDatetime


# Примечание: настройки пользователя, нужные Brain
class UserSettings(WireModel):
    tz: str
    defaultEventMin: int = Field(default=DEFAULT_EVENT_MIN, ge=1)
    voiceReplies: bool = False


# Примечание: вход хода: «сейчас» уже в поясе пользователя — Brain не считает пояса сам
class TurnInput(WireModel):
    text: str = Field(min_length=1)
    inputType: Literal["voice", "text"]
    asrLowConfidence: bool = False
    now: AwareDatetime
    tz: str
    history: list[Turn] = Field(default_factory=list, max_length=HISTORY_LIMIT)
    focus: list[ItemBrief] = Field(default_factory=list)
    pending: JsonObject | None = None
    settings: UserSettings

    # Примечание: пояс существует в tzdata
    @field_validator("tz")
    @classmethod
    def _checkTz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"неизвестный часовой пояс: {value}"
            raise ValueError(msg) from exc
        return value

    # Примечание: смещение now совпадает с поясом пользователя — иначе «завтра» уедет на сутки
    @model_validator(mode="after")
    def _nowInUserTz(self) -> Self:
        expected = datetime.fromtimestamp(self.now.timestamp(), ZoneInfo(self.tz)).utcoffset()
        if self.now.utcoffset() != expected:
            msg = f"now {self.now.isoformat()} не в поясе {self.tz} (ожидается смещение {expected})"
            raise ValueError(msg)
        return self


# Примечание: описание инструмента для LLM — уже без служебного caller (J3)
class ToolSpec(WireModel):
    name: str = Field(pattern=r"^[a-z][a-z_]*$")
    description: str
    inputSchema: JsonObject


# Примечание: ошибка инструмента в стандартном виде (code, message, details)
class ErrorInfo(WireModel):
    code: str
    message: str
    details: JsonObject = Field(default_factory=dict)


# Примечание: результат вызова: summary — строка для LLM, data — structuredContent для агента
class ToolResult(WireModel):
    ok: bool
    summary: str
    data: JsonObject | None = None
    error: ErrorInfo | None = None

    # Примечание: успех без ошибки, неуспех — с ошибкой; иначе агент не поймёт, что рисовать
    @model_validator(mode="after")
    def _okMatchesError(self) -> Self:
        if self.ok == (self.error is not None):
            msg = "ToolResult: ok=true без error или ok=false с error"
            raise ValueError(msg)
        return self


# Примечание: исполнитель инструментов для Brain; реализует C (McpToolExecutor), фейк — у B
class ToolExecutor(Protocol):
    # Примечание: список инструментов без caller
    async def listTools(self) -> list[ToolSpec]: ...

    # Примечание: вызов инструмента; caller и ключ идемпотентности подставляет C
    async def call(self, name: str, args: Mapping[str, object]) -> ToolResult: ...


# ===================== сторона B — НАБРОСОК, ВЛАДЕЛЕЦ B =====================


# Примечание: (B) уточнение: чего не хватает, готовые варианты-кнопки и непрозрачный pending
class Clarify(WireModel):
    missing: list[str] = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    pending: JsonObject


# Примечание: (B) выполненный вызов инструмента — источник подтверждения пользователю
class ExecutedAction(WireModel):
    name: str
    args: JsonObject
    result: ToolResult


# Примечание: (B) расход на ход — для логов и eval
class Usage(WireModel):
    model: str
    llmCalls: int = Field(ge=0)
    tokensIn: int = Field(ge=0)
    tokensOut: int = Field(ge=0)
    latencyMs: int = Field(ge=0)


type TurnOutcome = Literal["done", "clarify", "answer", "error"]
type BrainErrorCode = Literal["llm_timeout", "llm_rate_limited", "llm_bad_output", "llm_no_action"]
# Коды ошибок инструментов (J3, стандарты): ход закончился ошибкой инструмента (добавлено в S1)
type ToolErrorCode = Literal[
    "validation_error",
    "not_found",
    "past_time",
    "conflict",
    "rate_limited",
    "internal",
    "upstream_unavailable",
    "upstream_timeout",
]


# Примечание: (B) итог хода; ошибки LLM не выбрасываются, а приходят как outcome=error
class TurnResult(WireModel):
    outcome: TurnOutcome
    actions: list[ExecutedAction] = Field(default_factory=list)
    say: str | None = None
    clarify: Clarify | None = None
    focus: list[uuid.UUID] = Field(default_factory=list)
    errorCode: BrainErrorCode | ToolErrorCode | None = None
    usage: Usage

    # Примечание: clarify ⇔ outcome=clarify, error_code ⇔ outcome=error
    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.outcome == "clarify") != (self.clarify is not None):
            msg = "TurnResult: clarify заполняется только при outcome=clarify"
            raise ValueError(msg)
        if (self.outcome == "error") != (self.errorCode is not None):
            msg = "TurnResult: error_code заполняется только при outcome=error"
            raise ValueError(msg)
        return self


# Примечание: (B) интерфейс Brain; resume — продолжить уточнение по выбранному варианту без LLM
class Brain(Protocol):
    # Примечание: новый ход пользователя
    async def run(self, turn: TurnInput, tools: ToolExecutor) -> TurnResult: ...

    # Примечание: ответ на уточнение (кнопка варианта)
    async def resume(self, turn: TurnInput, choice: str, tools: ToolExecutor) -> TurnResult: ...
