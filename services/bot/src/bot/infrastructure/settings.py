import re
from typing import ClassVar, Final, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator

from bot.application.reply_mapper import OpenAppMode
from bot.infrastructure.audio_downloader import DEFAULT_MAX_BYTES
from bot.infrastructure.real_max import MAX_API_URL
from common.config import BaseServiceSettings

# MAX: секрет подписки — 5…256 символов [a-zA-Z0-9_-] (dev.max.ru, POST /subscriptions)
WEBHOOK_SECRET_RE: Final = re.compile(r"^[a-zA-Z0-9_-]{5,256}$")


# Примечание: настройки бота; свои переменные — только с префиксом BOT_
class BotSettings(BaseServiceSettings):
    envPrefix: ClassVar[str] = "BOT_"

    maxMode: Literal["fake", "real"] = Field(
        default="fake",
        validation_alias="BOT_MAX_MODE",
        description="`fake` — MAX в памяти и `/dev/max/*`; `real` — MAX Bot API (BOT_MAX_TOKEN)",
    )
    maxToken: SecretStr | None = Field(
        default=None,
        validation_alias="BOT_MAX_TOKEN",
        description="токен бота MAX (business.max.ru), обязателен для `real`",
    )
    maxApiUrl: str = Field(
        default=MAX_API_URL,
        validation_alias="BOT_MAX_API_URL",
        description="адрес MAX Bot API",
    )
    maxCaBundle: str | None = Field(
        default=None,
        validation_alias="BOT_MAX_CA_BUNDLE",
        description="путь к сертификату НУЦ Минцифры (им подписан API MAX); пусто — системные",
    )
    maxTimeoutS: float = Field(
        default=10.0,
        gt=0,
        le=30,
        validation_alias="BOT_MAX_TIMEOUT_S",
        description="таймаут запроса к MAX API, с (кроме long polling)",
    )
    pollTimeoutS: int = Field(
        default=30,
        ge=0,
        le=90,
        validation_alias="BOT_POLL_TIMEOUT_S",
        description="таймаут long polling, с (0–90)",
    )
    rateLimitPerMin: int = Field(
        default=20,
        ge=1,
        validation_alias="BOT_RATE_LIMIT_PER_MIN",
        description="сообщений пользователя в минуту, сверх — «Слишком часто»",
    )
    shutdownTimeoutS: float = Field(
        default=10.0,
        gt=0,
        validation_alias="BOT_SHUTDOWN_TIMEOUT_S",
        description="сколько ждать фоновые задачи на SIGTERM, с",
    )
    fakeSendDelayMs: int = Field(
        default=0,
        ge=0,
        validation_alias="BOT_FAKE_SEND_DELAY_MS",
        description="задержка «отправки» в фейковом MAX, мс (lifecycle-тесты)",
    )
    # Агент (API из contracts/reply.py): стандарт — 45 с без повторов
    agentUrl: str = Field(
        default="http://agent:8000",
        validation_alias="BOT_AGENT_URL",
        description="адрес агента (API из `contracts/reply.py`)",
    )
    agentTimeoutS: float = Field(
        default=45.0,
        gt=0,
        le=60,
        validation_alias="BOT_AGENT_TIMEOUT_S",
        description="сколько ждать агента, с; повторов нет (стандарт)",
    )
    # Кнопка мини-приложения: open_app (payload = start_param) или ссылка max.ru/<бот>?startapp=
    openAppMode: OpenAppMode = Field(
        default=OpenAppMode.OPEN_APP,
        validation_alias="BOT_OPEN_APP_MODE",
        description="кнопка мини-приложения: `open_app` или ссылка `link` (max.ru/<бот>?startapp=)",
    )
    # Имя бота в MAX: web_app у open_app и адрес deep link; выдают после модерации
    username: str = Field(
        default="",
        validation_alias="BOT_USERNAME",
        description="имя бота в MAX: `web_app` у open_app и адрес deep link",
    )
    # ASR (J1): 15 с и до 2 повторов (стандарт); голосовое скачиваем сразу, лимит — по байтам
    asrUrl: str = Field(
        default="http://asr:8000", validation_alias="BOT_ASR_URL", description="адрес ASR (J1)"
    )
    asrTimeoutS: float = Field(
        default=15.0,
        gt=0,
        le=30,
        validation_alias="BOT_ASR_TIMEOUT_S",
        description="таймаут ASR, с; 2 повтора с ключом от хеша аудио",
    )
    voiceMaxBytes: int = Field(
        default=DEFAULT_MAX_BYTES,
        gt=0,
        validation_alias="BOT_VOICE_MAX_BYTES",
        description="лимит голосового, байт; больше — «слишком длинное»",
    )
    voiceDownloadTimeoutS: float = Field(
        default=10.0,
        gt=0,
        validation_alias="BOT_VOICE_DOWNLOAD_TIMEOUT_S",
        description="таймаут скачивания голосового по ссылке MAX, с",
    )
    # Откуда бот скачивает голосовые фейкового MAX (/dev/max/files) — сам у себя
    fakeFilesUrl: str = Field(
        default="http://127.0.0.1:8000",
        validation_alias="BOT_FAKE_FILES_URL",
        description="откуда бот скачивает голосовые фейкового MAX (`/dev/max/files`) — сам у себя",
    )
    typingIntervalS: float = Field(
        default=4.0,
        gt=0,
        validation_alias="BOT_TYPING_INTERVAL_S",
        description="как часто повторять «печатает…», с",
    )
    # polling — GET /updates в цикле; webhook — POST /webhook/max от MAX (стенд с HTTPS)
    updatesMode: Literal["polling", "webhook"] = Field(
        default="polling",
        validation_alias="BOT_UPDATES_MODE",
        description="`polling` (GET /updates) или `webhook` (POST /webhook/max за Caddy)",
    )
    # Адрес подписки: у настоящего MAX — только https:// на 443 без порта в URL
    webhookUrl: str = Field(
        default="",
        validation_alias="BOT_WEBHOOK_URL",
        description="адрес подписки webhook; у настоящего MAX — только https:// на 443",
    )
    webhookSecret: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="BOT_WEBHOOK_SECRET",
        description="секрет подписки (5–256 символов a-zA-Z0-9_-), MAX шлёт в X-Max-Bot-Api-Secret",
    )
    # Общий предел одновременных обработок апдейтов (у одного пользователя — всегда по одному)
    maxConcurrentUpdates: int = Field(
        default=16,
        ge=1,
        le=256,
        validation_alias="BOT_MAX_CONCURRENT_UPDATES",
        description="апдейтов одновременно (у одного пользователя — по очереди)",
    )

    # Примечание: пустая строка из .env («BOT_MAX_TOKEN=») — «не задано», а не пустой токен
    @field_validator("maxToken", "maxCaBundle", mode="before")
    @classmethod
    def _emptyIsUnset(cls, value: object) -> object:
        return None if value == "" else value

    # Примечание: real без токена — ошибка на старте, а не 401 на каждом запросе к MAX
    @model_validator(mode="after")
    def _tokenForRealMax(self) -> Self:
        if self.maxMode == "real" and self.maxToken is None:
            msg = "BOT_MAX_MODE=real требует BOT_MAX_TOKEN"
            raise ValueError(msg)
        return self

    # Примечание: webhook без адреса или секрета не запускаем; настоящему MAX — только https
    @model_validator(mode="after")
    def _webhookConfigured(self) -> Self:
        if self.updatesMode != "webhook":
            return self
        if not self.webhookUrl:
            msg = "BOT_UPDATES_MODE=webhook требует BOT_WEBHOOK_URL"
            raise ValueError(msg)
        if self.maxMode == "real" and not self.webhookUrl.startswith("https://"):
            msg = "BOT_WEBHOOK_URL для MAX — только https:// (порт 443)"
            raise ValueError(msg)
        if not WEBHOOK_SECRET_RE.match(self.webhookSecret.get_secret_value()):
            msg = "BOT_WEBHOOK_SECRET: 5–256 символов из a-z, A-Z, 0-9, _ и -"
            raise ValueError(msg)
        return self
