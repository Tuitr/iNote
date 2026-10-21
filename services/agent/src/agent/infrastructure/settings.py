from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field

from common.config import BaseServiceSettings


# Примечание: настройки агента; свои переменные — только с префиксом AGENT_
class AgentSettings(BaseServiceSettings):
    envPrefix: ClassVar[str] = "AGENT_"

    # scripted — правила вместо LLM (моки, e2e); llm — Brain участника B после мержа
    brain: Literal["scripted", "llm"] = Field(default="scripted", validation_alias="AGENT_BRAIN")
    # Бюджет хода вместе с ожиданием лока: бот ждёт агента 45 с (docs/standards.md)
    turnTimeoutS: float = Field(default=40.0, gt=0, le=44, validation_alias="AGENT_TURN_TIMEOUT_S")
    lockWaitS: float = Field(default=10.0, ge=0, validation_alias="AGENT_LOCK_WAIT_S")
    # Одна MCP-сессия на процесс (J3); внутри compose — http://mcp:8000/mcp
    mcpUrl: str = Field(default="http://mcp:8000/mcp", validation_alias="AGENT_MCP_URL")
    # Служебный REST того же контейнера (J8): /internal/v1/users/resolve, /health/live
    mcpInternalUrl: str = Field(
        default="http://mcp:8000", validation_alias="AGENT_MCP_INTERNAL_URL"
    )
    # Стандарт: 5 с на вызов и 1 повтор (только с ключом идемпотентности)
    mcpTimeoutS: float = Field(default=5.0, gt=0, le=10, validation_alias="AGENT_MCP_TIMEOUT_S")
    # /v1/actions — заглушка до S2; путь относительно рабочего каталога (корень репо, /app)
    stubActionReply: Path = Field(
        default=Path("contracts/examples/reply/list.json"),
        validation_alias="AGENT_STUB_ACTION_REPLY",
    )
