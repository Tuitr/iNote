from pathlib import Path
from typing import ClassVar

from pydantic import Field

from common.config import BaseServiceSettings


# Примечание: настройки агента; свои переменные — только с префиксом AGENT_
class AgentSettings(BaseServiceSettings):
    envPrefix: ClassVar[str] = "AGENT_"

    # Пути относительно рабочего каталога (корень репо локально, /app в контейнере)
    stubMessageReply: Path = Field(
        default=Path("contracts/examples/reply/confirm.json"),
        validation_alias="AGENT_STUB_MESSAGE_REPLY",
    )
    stubActionReply: Path = Field(
        default=Path("contracts/examples/reply/list.json"),
        validation_alias="AGENT_STUB_ACTION_REPLY",
    )
