from typing import ClassVar, Literal

from pydantic import Field

from bot.application.reply_mapper import OpenAppMode
from common.config import BaseServiceSettings


# Примечание: настройки бота; свои переменные — только с префиксом BOT_
class BotSettings(BaseServiceSettings):
    envPrefix: ClassVar[str] = "BOT_"

    maxMode: Literal["fake", "real"] = Field(default="fake", validation_alias="BOT_MAX_MODE")
    pollTimeoutS: int = Field(default=30, ge=0, le=90, validation_alias="BOT_POLL_TIMEOUT_S")
    rateLimitPerMin: int = Field(default=20, ge=1, validation_alias="BOT_RATE_LIMIT_PER_MIN")
    shutdownTimeoutS: float = Field(default=10.0, gt=0, validation_alias="BOT_SHUTDOWN_TIMEOUT_S")
    fakeSendDelayMs: int = Field(default=0, ge=0, validation_alias="BOT_FAKE_SEND_DELAY_MS")
    # Агент (API из contracts/reply.py): стандарт — 45 с без повторов
    agentUrl: str = Field(default="http://agent:8000", validation_alias="BOT_AGENT_URL")
    agentTimeoutS: float = Field(default=45.0, gt=0, le=60, validation_alias="BOT_AGENT_TIMEOUT_S")
    # Кнопка мини-приложения: open_app (payload = start_param) или ссылка max.ru/<бот>?startapp=
    openAppMode: OpenAppMode = Field(
        default=OpenAppMode.OPEN_APP, validation_alias="BOT_OPEN_APP_MODE"
    )
    # Имя бота в MAX: web_app у open_app и адрес deep link; выдают после модерации
    username: str = Field(default="", validation_alias="BOT_USERNAME")
    typingIntervalS: float = Field(default=4.0, gt=0, validation_alias="BOT_TYPING_INTERVAL_S")
