"""Настройки ASR: свои переменные — только с префиксом ASR_ (общие — из BaseServiceSettings).

Бюджет времени: бот ждёт ASR 15 с на попытку (стандарт), поэтому весь запрос к провайдеру вместе
с повтором укладывается в ASR_DEADLINE_S < 15 с — иначе бот бросит ответ, за который уже заплачено.
"""

from typing import ClassVar, Final, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator

from common.config import BaseServiceSettings

YANDEX_STT_URL: Final = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
# Синхронное распознавание SpeechKit v1: до 1 МБ и до 30 с, один канал
YANDEX_MAX_BYTES: Final = 1024 * 1024
YANDEX_MAX_DURATION_S: Final = 30
# бот ждёт одну попытку 15 с (BOT_ASR_TIMEOUT_S)
BOT_ATTEMPT_TIMEOUT_S: Final = 15.0

type Provider = Literal["fake", "yandex"]


# Примечание: настройки сервиса asr
class AsrSettings(BaseServiceSettings):
    envPrefix: ClassVar[str] = "ASR_"

    provider: Provider = Field(
        default="fake",
        validation_alias="ASR_PROVIDER",
        description="`fake` — «OggS» + текст, без ключей и денег; `yandex` — SpeechKit (платно)",
    )
    yandexApiKey: SecretStr | None = Field(
        default=None,
        validation_alias="ASR_YANDEX_API_KEY",
        description="API-ключ сервисного аккаунта (роль `ai.speechkit-stt.user`), для `yandex`",
    )
    yandexFolderId: str | None = Field(
        default=None,
        max_length=50,
        validation_alias="ASR_YANDEX_FOLDER_ID",
        description="id каталога — только для ключа пользователя; для сервисного аккаунта пусто",
    )
    yandexUrl: str = Field(
        default=YANDEX_STT_URL,
        validation_alias="ASR_YANDEX_URL",
        description="адрес синхронного распознавания SpeechKit v1",
    )
    providerTimeoutS: float = Field(
        default=6.0,
        gt=0,
        validation_alias="ASR_PROVIDER_TIMEOUT_S",
        description="таймаут одной попытки к провайдеру, с",
    )
    providerRetries: int = Field(
        default=1,
        ge=0,
        le=2,
        validation_alias="ASR_PROVIDER_RETRIES",
        description="повторы к провайдеру на 5xx и сетевые ошибки (в пределах ASR_DEADLINE_S)",
    )
    deadlineS: float = Field(
        default=13.0,
        gt=0,
        lt=BOT_ATTEMPT_TIMEOUT_S,
        validation_alias="ASR_DEADLINE_S",
        description="весь запрос к провайдеру с повторами, с; меньше таймаута бота (15 с)",
    )
    maxBytes: int = Field(
        default=YANDEX_MAX_BYTES,
        gt=0,
        validation_alias="ASR_MAX_BYTES",
        description="лимит голосового, байт; больше — `audio_too_long` (SpeechKit — до 1 МБ)",
    )
    maxDurationS: int = Field(
        default=YANDEX_MAX_DURATION_S,
        gt=0,
        validation_alias="ASR_MAX_DURATION_S",
        description="лимит длительности, с; дольше — `audio_too_long` (SpeechKit — до 30 с)",
    )
    lowConfidenceBelow: float = Field(
        default=0.6,
        ge=0,
        le=1,
        validation_alias="ASR_LOW_CONFIDENCE_BELOW",
        description="уверенность ниже — `low_confidence` (если провайдер её сообщает)",
    )

    # Примечание: пустая строка из .env — «не задано»
    @field_validator("yandexApiKey", "yandexFolderId", mode="before")
    @classmethod
    def _emptyIsNone(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

    # Примечание: SpeechKit без ключа не стартует; лимиты не шире синхронного распознавания
    @model_validator(mode="after")
    def _checkProvider(self) -> Self:
        if self.provider != "yandex":
            return self
        if self.yandexApiKey is None:
            msg = "ASR_PROVIDER=yandex: нужен ASR_YANDEX_API_KEY"
            raise ValueError(msg)
        if self.maxBytes > YANDEX_MAX_BYTES or self.maxDurationS > YANDEX_MAX_DURATION_S:
            msg = (
                f"SpeechKit синхронно принимает до {YANDEX_MAX_BYTES} байт и "
                f"{YANDEX_MAX_DURATION_S} с: уменьшите ASR_MAX_BYTES и ASR_MAX_DURATION_S"
            )
            raise ValueError(msg)
        return self
