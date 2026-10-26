"""AsrSettings: SpeechKit без ключа не стартует, лимиты — как у синхронного API, бюджет < бота."""

import pytest
from pydantic import ValidationError

from asr.infrastructure.settings import (
    BOT_ATTEMPT_TIMEOUT_S,
    YANDEX_MAX_BYTES,
    YANDEX_MAX_DURATION_S,
    AsrSettings,
)

TOKEN = "t" * 32


# Примечание: окружение с токеном и переменными теста
@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in AsrSettings.envNames():
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SERVICE_TOKEN", TOKEN)
    return monkeypatch


# Примечание: по умолчанию — фейк, лимиты SpeechKit, бюджет меньше таймаута бота
def test_defaults(env: pytest.MonkeyPatch) -> None:
    settings = AsrSettings()

    assert settings.provider == "fake"
    assert (settings.maxBytes, settings.maxDurationS) == (YANDEX_MAX_BYTES, YANDEX_MAX_DURATION_S)
    assert settings.deadlineS < BOT_ATTEMPT_TIMEOUT_S
    # две попытки с таймаутом каждой укладываются в бюджет
    assert settings.providerTimeoutS * (settings.providerRetries + 1) <= settings.deadlineS


# Примечание: yandex без ключа — ошибка на старте, а не 503 на первом голосовом
@pytest.mark.parametrize("key", [None, "", "   "])
def test_yandexNeedsKey(env: pytest.MonkeyPatch, key: str | None) -> None:
    env.setenv("ASR_PROVIDER", "yandex")
    if key is not None:
        env.setenv("ASR_YANDEX_API_KEY", key)

    with pytest.raises(ValidationError, match="ASR_YANDEX_API_KEY"):
        AsrSettings()


# Примечание: ключ — SecretStr: в repr и в дампе настроек его нет
def test_keyIsSecret(env: pytest.MonkeyPatch) -> None:
    env.setenv("ASR_PROVIDER", "yandex")
    env.setenv("ASR_YANDEX_API_KEY", "AQVN-secret")
    env.setenv("ASR_YANDEX_FOLDER_ID", "")

    settings = AsrSettings()

    assert settings.yandexApiKey is not None
    assert settings.yandexApiKey.get_secret_value() == "AQVN-secret"
    assert "AQVN-secret" not in repr(settings)
    assert settings.yandexFolderId is None


@pytest.mark.parametrize(
    ("name", "value"),
    [("ASR_MAX_BYTES", str(YANDEX_MAX_BYTES + 1)), ("ASR_MAX_DURATION_S", "31")],
)
# Примечание: SpeechKit синхронно — до 1 МБ и 30 с: шире нельзя
def test_yandexLimits(env: pytest.MonkeyPatch, name: str, value: str) -> None:
    env.setenv("ASR_PROVIDER", "yandex")
    env.setenv("ASR_YANDEX_API_KEY", "key")
    env.setenv(name, value)

    with pytest.raises(ValidationError, match="SpeechKit"):
        AsrSettings()


# Примечание: бюджет не может быть больше таймаута бота
def test_deadlineBelowBotTimeout(env: pytest.MonkeyPatch) -> None:
    env.setenv("ASR_DEADLINE_S", str(BOT_ATTEMPT_TIMEOUT_S))

    with pytest.raises(ValidationError):
        AsrSettings()


# Примечание: фейку лимиты SpeechKit не обязательны (стенд mock)
def test_fakeAllowsWiderLimits(env: pytest.MonkeyPatch) -> None:
    env.setenv("ASR_MAX_BYTES", str(2 * YANDEX_MAX_BYTES))

    assert AsrSettings().maxBytes == 2 * YANDEX_MAX_BYTES
