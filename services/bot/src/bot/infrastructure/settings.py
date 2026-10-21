from typing import ClassVar, Literal

from pydantic import Field

from common.config import BaseServiceSettings


# Примечание: настройки бота; свои переменные — только с префиксом BOT_
class BotSettings(BaseServiceSettings):
    envPrefix: ClassVar[str] = "BOT_"

    maxMode: Literal["fake", "real"] = Field(default="fake", validation_alias="BOT_MAX_MODE")
    pollTimeoutS: int = Field(default=30, ge=0, le=90, validation_alias="BOT_POLL_TIMEOUT_S")
    rateLimitPerMin: int = Field(default=20, ge=1, validation_alias="BOT_RATE_LIMIT_PER_MIN")
    shutdownTimeoutS: float = Field(default=10.0, gt=0, validation_alias="BOT_SHUTDOWN_TIMEOUT_S")
    fakeSendDelayMs: int = Field(default=0, ge=0, validation_alias="BOT_FAKE_SEND_DELAY_MS")
