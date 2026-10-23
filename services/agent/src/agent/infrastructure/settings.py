from typing import ClassVar, Literal

from pydantic import Field

from common.config import BaseServiceSettings


# Примечание: настройки агента; свои переменные — только с префиксом AGENT_
class AgentSettings(BaseServiceSettings):
    envPrefix: ClassVar[str] = "AGENT_"

    # scripted — правила вместо LLM (моки, e2e); llm — Brain участника B после мержа
    brain: Literal["scripted", "llm"] = Field(
        default="scripted",
        validation_alias="AGENT_BRAIN",
        description="`scripted` — правила вместо LLM (mock, e2e); `llm` — Brain B (после мержа)",
    )
    # Бюджет хода вместе с ожиданием лока: бот ждёт агента 45 с (docs/standards.md)
    turnTimeoutS: float = Field(
        default=40.0,
        gt=0,
        le=44,
        validation_alias="AGENT_TURN_TIMEOUT_S",
        description="бюджет хода вместе с ожиданием лока, с (меньше ожидания бота — 45 с)",
    )
    lockWaitS: float = Field(
        default=10.0,
        ge=0,
        validation_alias="AGENT_LOCK_WAIT_S",
        description="сколько ждать лок пользователя, с; не дождались — «Ещё обрабатываю…»",
    )
    # Одна MCP-сессия на процесс (J3); внутри compose — http://mcp:8000/mcp
    mcpUrl: str = Field(
        default="http://mcp:8000/mcp",
        validation_alias="AGENT_MCP_URL",
        description="MCP (J3), одна сессия Streamable HTTP на процесс",
    )
    # Служебный REST того же контейнера (J8): /internal/v1/users/resolve, /health/live
    mcpInternalUrl: str = Field(
        default="http://mcp:8000",
        validation_alias="AGENT_MCP_INTERNAL_URL",
        description="служебный REST mcp-контейнера (J8) и его /health/live",
    )
    # Стандарт: 5 с на вызов и 1 повтор (только с ключом идемпотентности)
    mcpTimeoutS: float = Field(
        default=5.0,
        gt=0,
        le=10,
        validation_alias="AGENT_MCP_TIMEOUT_S",
        description="таймаут вызова MCP и J8, с; 1 повтор с тем же ключом",
    )
