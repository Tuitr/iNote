from pathlib import Path
from typing import ClassVar

from pydantic import Field

from common.config import BaseServiceSettings


# Примечание: настройки агента; свои переменные — только с префиксом AGENT_
class AgentSettings(BaseServiceSettings):
    envPrefix: ClassVar[str] = "AGENT_"

    # Бюджет хода вместе с ожиданием лока: бот ждёт агента 45 с (docs/standards.md)
    turnTimeoutS: float = Field(default=40.0, gt=0, le=44, validation_alias="AGENT_TURN_TIMEOUT_S")
    lockWaitS: float = Field(default=10.0, ge=0, validation_alias="AGENT_LOCK_WAIT_S")
    # /v1/actions — заглушка до S2; путь относительно рабочего каталога (корень репо, /app)
    stubActionReply: Path = Field(
        default=Path("contracts/examples/reply/list.json"),
        validation_alias="AGENT_STUB_ACTION_REPLY",
    )
