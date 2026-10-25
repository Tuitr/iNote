"""LLMClient: один вызов модели с инструментами и один формат ответа для любого провайдера.

Ошибки провайдера приходят исключением LlmError с кодом из J2 (llm_timeout, llm_rate_limited,
llm_bad_output); наружу Brain их не выпускает — превращает в TurnResult с outcome=error.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

from contracts.agent_side import ToolSpec

type JsonObject = dict[str, object]
type Role = Literal["system", "user", "assistant", "function"]
type LlmErrorCode = Literal["llm_timeout", "llm_rate_limited", "llm_bad_output"]


# Примечание: модель хочет вызвать инструмент: имя и аргументы уже объектом
@dataclass(frozen=True)
class FunctionCall:
    name: str
    arguments: JsonObject


# Примечание: сообщение диалога с моделью; function — результат инструмента (name — его имя)
@dataclass(frozen=True)
class LlmMessage:
    role: Role
    content: str
    functionCall: FunctionCall | None = None
    name: str | None = None
    # functions_state_id GigaChat: связывает вызов функции с её результатом в истории
    stateId: str | None = None


# Примечание: ответ модели: текст или вызов инструмента, плюс расход для Usage
@dataclass(frozen=True)
class LlmReply:
    text: str
    functionCall: FunctionCall | None
    model: str
    tokensIn: int = 0
    tokensOut: int = 0
    latencyMs: int = 0
    stateId: str | None = None


# Примечание: провайдер не ответил по делу; code — из перечня ошибок Brain в J2
class LlmError(Exception):
    def __init__(self, code: LlmErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: LlmErrorCode = code


# Примечание: клиент модели; temperature=0 — стандарт B (ответы как можно стабильнее)
class LlmClient(Protocol):
    async def complete(
        self,
        messages: Sequence[LlmMessage],
        tools: Sequence[ToolSpec],
        temperature: float = 0.0,
    ) -> LlmReply: ...


# Примечание: что получил фейк при вызове — для проверок в тестах
@dataclass(frozen=True)
class FakeCall:
    messages: tuple[LlmMessage, ...]
    tools: tuple[ToolSpec, ...]
    temperature: float


# Примечание: модель без сети и денег: отдаёт заранее заданные ответы (или ошибки) по очереди
@dataclass
class FakeLlmClient:
    script: list[LlmReply | LlmError]
    calls: list[FakeCall] = field(default_factory=list)

    async def complete(
        self,
        messages: Sequence[LlmMessage],
        tools: Sequence[ToolSpec],
        temperature: float = 0.0,
    ) -> LlmReply:
        self.calls.append(FakeCall(tuple(messages), tuple(tools), temperature))
        if not self.script:
            msg = "FakeLlmClient: ответы сценария закончились"
            raise LlmError("llm_bad_output", msg)
        step = self.script.pop(0)
        if isinstance(step, LlmError):
            raise step
        return step
