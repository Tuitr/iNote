"""SpeechToText на Yandex SpeechKit v1: синхронное распознавание коротких аудио (до 30 с и 1 МБ).

POST stt:recognize?lang=…&format=oggopus&rawResults=false, тело — байты OGG/Opus как есть,
`Authorization: Api-Key <ключ>`. Ответ — `{"result": "текст"}` строчными; уверенности в v1 нет.
Повторы — только на 5xx, таймаут попытки и сетевой сбой, и только пока не вышел общий бюджет.
401/403 (ключ, роль), 429 (квота) и 4xx — без повторов. Ключ и аудио в логи не попадают.
"""

import asyncio
import time
from typing import Final

import httpx
from pydantic import SecretStr

from asr.application.ports import AudioClip, Recognition
from asr.domain.failures import Failure, TranscribeError
from asr.domain.transcript import capitalizeFirst
from common.logging import getLogger

PROVIDER_NAME: Final = "yandex"
AUDIO_FORMAT: Final = "oggopus"
TOPIC: Final = "general"
RETRY_DELAY_S: Final = 0.3
_AUTH_STATUSES: Final = frozenset({401, 403})
_RATE_LIMITED: Final = 429
_SERVER_ERROR: Final = 500

_log = getLogger(__name__)


# Примечание: адаптер SpeechKit; http-клиент создаётся в lifespan и закрывается там же (правило 9)
class YandexSpeechKit:
    # Примечание: attemptTimeoutS — одна попытка, deadlineS — все попытки вместе
    def __init__(
        self,
        http: httpx.AsyncClient,
        apiKey: SecretStr,
        *,
        url: str,
        folderId: str | None = None,
        attemptTimeoutS: float,
        retries: int,
        deadlineS: float,
        retryDelayS: float = RETRY_DELAY_S,
    ) -> None:
        self._http = http
        self._apiKey = apiKey
        self._url = url
        self._folderId = folderId
        self._attemptTimeoutS = attemptTimeoutS
        self._retries = retries
        self._deadlineS = deadlineS
        self._retryDelayS = retryDelayS

    # Примечание: имя для логов
    @property
    def name(self) -> str:
        return PROVIDER_NAME

    # Примечание: попытки в пределах бюджета; вышел бюджет — upstream_timeout
    async def recognize(self, clip: AudioClip) -> Recognition:
        started = time.monotonic()
        last = TranscribeError(Failure.UPSTREAM_UNAVAILABLE, "SpeechKit недоступен")
        try:
            async with asyncio.timeout(self._deadlineS):
                for attempt in range(1, self._retries + 2):
                    if attempt > 1:
                        await asyncio.sleep(self._retryDelayS * 2 ** (attempt - 2))
                    try:
                        return await self._attempt(clip, attempt, started)
                    except _RetryableError as exc:
                        last = exc.error
        except TimeoutError:
            _log.warning(
                "asr_provider_deadline", provider=PROVIDER_NAME, deadline_s=self._deadlineS
            )
            raise TranscribeError(
                Failure.UPSTREAM_TIMEOUT, "SpeechKit не ответил вовремя"
            ) from None
        raise last

    # Примечание: одна попытка: временный сбой — _RetryableError, окончательный — TranscribeError
    async def _attempt(self, clip: AudioClip, attempt: int, started: float) -> Recognition:
        try:
            response = await self._http.post(
                self._url,
                params=self._params(clip.lang),
                content=clip.content,
                headers={"Authorization": f"Api-Key {self._apiKey.get_secret_value()}"},
                timeout=self._attemptTimeoutS,
            )
        except httpx.TimeoutException:
            _log.warning("asr_provider_timeout", provider=PROVIDER_NAME, attempt=attempt)
            raise _RetryableError(
                TranscribeError(Failure.UPSTREAM_TIMEOUT, "SpeechKit не ответил вовремя")
            ) from None
        except httpx.TransportError as exc:
            _log.warning(
                "asr_provider_unreachable",
                provider=PROVIDER_NAME,
                attempt=attempt,
                error=type(exc).__name__,
            )
            raise _RetryableError(
                TranscribeError(Failure.UPSTREAM_UNAVAILABLE, "SpeechKit недоступен")
            ) from None
        durationMs = round((time.monotonic() - started) * 1000)
        if response.is_success:
            return _recognition(response, attempt, durationMs)
        raise _failure(response, attempt)

    # Примечание: параметры запроса; folderId — только если задан (ключ пользователя)
    def _params(self, lang: str) -> dict[str, str]:
        params = {"lang": lang, "format": AUDIO_FORMAT, "topic": TOPIC, "rawResults": "false"}
        if self._folderId:
            params["folderId"] = self._folderId
        return params


# Примечание: временный сбой провайдера — повторяем, пока есть бюджет
class _RetryableError(Exception):
    # Примечание: error — что вернуть, если повторы кончились
    def __init__(self, error: TranscribeError) -> None:
        super().__init__(error.message)
        self.error = error


# Примечание: разбор 200: {"result": "…"}; другой ответ — провайдер сломан, а не «тишина»
def _recognition(response: httpx.Response, attempt: int, durationMs: int) -> Recognition:
    try:
        body = response.json()
    except ValueError:
        body = None
    result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, str):
        _log.error("asr_provider_bad_response", provider=PROVIDER_NAME, status=response.status_code)
        raise TranscribeError(Failure.UPSTREAM_UNAVAILABLE, "SpeechKit ответил не по формату")
    _log.info(
        "asr_provider_recognized",
        provider=PROVIDER_NAME,
        attempt=attempt,
        provider_ms=durationMs,
        text_length=len(result),
    )
    return Recognition(text=capitalizeFirst(result.strip()), confidence=None, raw=result)


# Примечание: не-2xx → окончательная ошибка или повтор (5xx); в лог — статус и код SpeechKit
def _failure(response: httpx.Response, attempt: int) -> Exception:
    status = response.status_code
    code = _errorCode(response)
    details: dict[str, object] = {"provider_status": status}
    if status >= _SERVER_ERROR:
        _log.warning("asr_provider_failed", provider=PROVIDER_NAME, attempt=attempt, status=status)
        return _RetryableError(
            TranscribeError(Failure.UPSTREAM_UNAVAILABLE, "SpeechKit недоступен", details)
        )
    if status in _AUTH_STATUSES:
        # неверный ключ или нет роли ai.speechkit-stt.user — ошибка настройки, а не пользователя
        _log.error("asr_provider_auth_failed", provider=PROVIDER_NAME, status=status, code=code)
        return TranscribeError(Failure.UPSTREAM_UNAVAILABLE, "SpeechKit отклонил ключ", details)
    if status == _RATE_LIMITED:
        _log.warning("asr_provider_rate_limited", provider=PROVIDER_NAME)
        return TranscribeError(Failure.UPSTREAM_UNAVAILABLE, "квота SpeechKit исчерпана", details)
    # 400 и прочие 4xx: SpeechKit не смог разобрать аудио
    _log.warning("asr_provider_rejected", provider=PROVIDER_NAME, status=status, code=code)
    return TranscribeError(Failure.UNSUPPORTED_FORMAT, "SpeechKit не принял аудио", details)


# Примечание: error_code из тела ошибки SpeechKit (без error_message — там может быть что угодно)
def _errorCode(response: httpx.Response) -> str | None:
    try:
        body = response.json()
    except ValueError:
        return None
    code = body.get("error_code") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None
