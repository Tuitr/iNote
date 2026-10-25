"""Настройки Brain: свои переменные — только с префиксом LLM_ (стандарт, docs/standards.md).

Brain живёт в процессе агента, поэтому общие переменные (SERVICE_TOKEN и др.) у него те же, что
у агента. Провайдер по умолчанию — fake: без ключа и денег; gigachat включается переменной.
"""

from typing import ClassVar, Final, Literal, Self

from pydantic import Field, SecretStr, field_validator, model_validator

from common.config import BaseServiceSettings

GIGACHAT_AUTH_URL: Final = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
GIGACHAT_API_URL: Final = "https://gigachat.devices.sberbank.ru/api/v1"
# Агент ждёт ход 40 с (AGENT_TURN_TIMEOUT_S): два вызова модели по 20 с с запасом не влезут
MAX_CALL_TIMEOUT_S: Final = 20.0
# Повтор и запасная модель должны уместиться в бюджет хода: 12 + 1 + 12 = 25 с
DEFAULT_CALL_TIMEOUT_S: Final = 12.0
DEFAULT_TURN_BUDGET_S: Final = 25.0
# Ход агента 40 с включает ожидание лока (до 10 с) — модели остаётся не больше 30
MAX_TURN_BUDGET_S: Final = 30.0

type Provider = Literal["fake", "gigachat"]
type GigaChatScope = Literal["GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP"]


# Примечание: настройки Brain внутри агента
class LlmSettings(BaseServiceSettings):
    envPrefix: ClassVar[str] = "LLM_"

    provider: Provider = Field(
        default="fake",
        validation_alias="LLM_PROVIDER",
        description="`fake` — заданные ответы, без сети и денег; `gigachat` — GigaChat (платно)",
    )
    model: str = Field(
        default="GigaChat-2-Max",
        min_length=1,
        validation_alias="LLM_MODEL",
        description="модель GigaChat",
    )
    fallbackModel: str | None = Field(
        default="GigaChat-2-Pro",
        validation_alias="LLM_FALLBACK_MODEL",
        description="запасная модель, если основная недоступна; пусто — без запасной",
    )
    timeoutS: float = Field(
        default=DEFAULT_CALL_TIMEOUT_S,
        gt=0,
        le=MAX_CALL_TIMEOUT_S,
        validation_alias="LLM_TIMEOUT_S",
        description="таймаут одного вызова модели, с (не больше 20 — бюджет хода агента 40 с)",
    )
    turnBudgetS: float = Field(
        default=DEFAULT_TURN_BUDGET_S,
        gt=0,
        le=MAX_TURN_BUDGET_S,
        validation_alias="LLM_TURN_BUDGET_S",
        description="сколько всего модель может думать за ход вместе с повторами, с (≤ 30)",
    )
    maxSteps: int = Field(
        default=4,
        ge=1,
        le=6,
        validation_alias="LLM_MAX_STEPS",
        description="сколько раз за ход модель может вызвать инструменты",
    )
    gigachatCredentials: SecretStr | None = Field(
        default=None,
        validation_alias="LLM_GIGACHAT_CREDENTIALS",
        description="ключ авторизации GigaChat (Base64 из личного кабинета), нужен для `gigachat`",
    )
    gigachatScope: GigaChatScope = Field(
        default="GIGACHAT_API_PERS",
        validation_alias="LLM_GIGACHAT_SCOPE",
        description="версия API: `GIGACHAT_API_PERS` — физлица, `_B2B` и `_CORP` — юрлица",
    )
    gigachatCaBundle: str | None = Field(
        default=None,
        validation_alias="LLM_GIGACHAT_CA_BUNDLE",
        description="путь к сертификату НУЦ Минцифры (GigaChat подписан им); пусто — системные",
    )
    gigachatAuthUrl: str = Field(
        default=GIGACHAT_AUTH_URL,
        validation_alias="LLM_GIGACHAT_AUTH_URL",
        description="адрес выдачи токена доступа (OAuth)",
    )
    gigachatApiUrl: str = Field(
        default=GIGACHAT_API_URL,
        validation_alias="LLM_GIGACHAT_API_URL",
        description="адрес API GigaChat (chat/completions)",
    )

    # Примечание: пустая строка из .env («LLM_GIGACHAT_CREDENTIALS=») — «не задано», не пустой ключ
    @field_validator("gigachatCredentials", "gigachatCaBundle", "fallbackModel", mode="before")
    @classmethod
    def _emptyIsUnset(cls, value: object) -> object:
        return None if value == "" else value

    # Примечание: gigachat без ключа — ошибка на старте, а не «Что-то пошло не так» на первом ходе
    @model_validator(mode="after")
    def _credentialsForGigaChat(self) -> Self:
        if self.provider == "gigachat" and self.gigachatCredentials is None:
            msg = "LLM_PROVIDER=gigachat: нужен LLM_GIGACHAT_CREDENTIALS"
            raise ValueError(msg)
        return self
