from collections.abc import Mapping
from dataclasses import dataclass
from types import TracebackType
from typing import Final, Self

import httpx
from pydantic import SecretStr, ValidationError
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from common.errors import AppError, ErrorCode, ErrorEnvelope
from common.logging import getLogger
from common.trace import TRACE_ID_HEADER, currentTraceId, newTraceId

IDEMPOTENT_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS", "PUT", "DELETE"})
IDEMPOTENCY_KEY_HEADER: Final = "Idempotency-Key"
RETRYABLE_STATUSES: Final = frozenset({429, 500, 502, 503, 504})

_log = getLogger(__name__)


# Примечание: политика повторов из стандарта: retries — число ПОВТОРОВ сверх первой попытки
@dataclass(frozen=True)
class RetryPolicy:
    retries: int = 0
    initialDelayS: float = 0.2
    maxDelayS: float = 2.0


NO_RETRY: Final = RetryPolicy()


# Примечание: ответ, который стоит повторить (429, 5xx) — внутреннее исключение для tenacity
class _RetryableStatusError(Exception):
    # Примечание: держим ответ, чтобы после исчерпания попыток разобрать его конверт
    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


# Примечание: повторяем сетевые сбои и «временные» статусы, но не 4xx
def _isRetryable(exc: BaseException) -> bool:
    return isinstance(exc, (httpx.TransportError, _RetryableStatusError))


# Примечание: HTTP-клиент между сервисами: таймаут, X-Trace-Id, токен, повторы, AppError
class ServiceHttpClient:
    # Примечание: создаётся один раз в lifespan (правило 9); transport — для respx и тестов
    def __init__(
        self,
        *,
        baseUrl: str,
        timeoutS: float,
        serviceToken: SecretStr | None = None,
        retry: RetryPolicy = NO_RETRY,
        upstreamName: str = "upstream",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._retry = retry
        self._upstream = upstreamName
        headers = {}
        if serviceToken is not None:
            headers["Authorization"] = f"Bearer {serviceToken.get_secret_value()}"
        self._client = httpx.AsyncClient(
            base_url=baseUrl, timeout=timeoutS, headers=headers, transport=transport
        )

    # Примечание: GET идемпотентен — повторяется по политике клиента
    async def get(
        self, path: str, *, params: Mapping[str, str] | None = None, retry: bool = True
    ) -> httpx.Response:
        return await self.request("GET", path, params=params, retry=retry)

    # Примечание: POST повторяется только с ключом идемпотентности (правило 6)
    async def post(
        self, path: str, *, json: object = None, idempotencyKey: str | None = None
    ) -> httpx.Response:
        return await self.request("POST", path, json=json, idempotencyKey=idempotencyKey)

    # Примечание: общий вызов; не-2xx превращается в AppError (конверт соседа или upstream_*)
    async def request(
        self,
        method: str,
        path: str,
        *,
        json: object = None,
        params: Mapping[str, str] | None = None,
        idempotencyKey: str | None = None,
        retry: bool = True,
    ) -> httpx.Response:
        method = method.upper()
        headers = {TRACE_ID_HEADER: currentTraceId() or newTraceId()}
        if idempotencyKey is not None:
            headers[IDEMPOTENCY_KEY_HEADER] = idempotencyKey
        canRetry = retry and (method in IDEMPOTENT_METHODS or idempotencyKey is not None)
        attempts = 1 + (self._retry.retries if canRetry else 0)

        # Примечание: одна попытка; «временный» статус поднимаем исключением для tenacity
        async def attempt() -> httpx.Response:
            response = await self._client.request(
                method, path, json=json, params=params, headers=headers
            )
            if response.status_code in RETRYABLE_STATUSES:
                raise _RetryableStatusError(response)
            return response

        retrying = AsyncRetrying(
            stop=stop_after_attempt(attempts),
            wait=wait_exponential_jitter(
                multiplier=self._retry.initialDelayS, max=self._retry.maxDelayS
            ),
            retry=retry_if_exception(_isRetryable),
            before_sleep=self._logRetry(method, path),
            reraise=True,
        )
        try:
            response: httpx.Response = await retrying(attempt)
        except _RetryableStatusError as exc:
            response = exc.response
        except httpx.TimeoutException as exc:
            raise AppError(
                ErrorCode.UPSTREAM_TIMEOUT, f"{self._upstream} не ответил вовремя"
            ) from exc
        except httpx.TransportError as exc:
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, f"{self._upstream} недоступен") from exc
        if response.is_success:
            return response
        raise self._toAppError(response)

    # Примечание: лог перед повтором — номер попытки и причина, без тела запроса
    def _logRetry(self, method: str, path: str) -> "_RetryLogger":
        return _RetryLogger(upstream=self._upstream, method=method, path=path)

    # Примечание: конверт ошибки соседа сохраняем как есть; без конверта — upstream_* или internal
    def _toAppError(self, response: httpx.Response) -> AppError:
        try:
            body = ErrorEnvelope.fromJson(response.content).error
        except ValidationError:
            code = (
                ErrorCode.UPSTREAM_UNAVAILABLE if response.is_server_error else ErrorCode.INTERNAL
            )
            return AppError(
                code,
                f"{self._upstream} ответил {response.status_code} без конверта ошибки",
                {"upstream_status": response.status_code},
            )
        details = {**body.details, "upstream": self._upstream}
        return AppError(body.code, body.message, details, httpStatus=response.status_code)

    # Примечание: закрыть пул соединений (на остановке lifespan)
    async def aclose(self) -> None:
        await self._client.aclose()

    # Примечание: async with — для тестов и скриптов
    async def __aenter__(self) -> Self:
        return self

    # Примечание: закрывает клиент при выходе из async with
    async def __aexit__(
        self,
        excType: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


# Примечание: колбэк tenacity before_sleep с контекстом вызова
@dataclass(frozen=True)
class _RetryLogger:
    upstream: str
    method: str
    path: str

    # Примечание: пишет warning с номером попытки и паузой
    def __call__(self, state: RetryCallState) -> None:
        outcome = state.outcome
        reason = type(outcome.exception()).__name__ if outcome and outcome.failed else "unknown"
        _log.warning(
            "http_retry",
            upstream=self.upstream,
            method=self.method,
            path=self.path,
            attempt=state.attempt_number,
            reason=reason,
            sleep_s=round(state.upcoming_sleep, 3),
        )
