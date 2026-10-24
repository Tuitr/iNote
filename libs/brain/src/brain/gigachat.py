"""LlmClient на GigaChat: REST напрямую, без SDK.

Токен доступа: POST {auth_url} c `Authorization: Basic <ключ авторизации>`, `RqUID` и `scope`;
живёт 30 мин — берём заново за минуту до конца или после 401. Вызов модели:
POST {api_url}/chat/completions с `functions` (JSON Schema аргументов) и `function_call: "auto"`;
вызов инструмента приходит в `choices[0].message.function_call` с `arguments` объектом.
Повтор — один, на 429, 5xx и таймаут; если основная модель так и не ответила — одна попытка
запасной. Ключ, токен и тексты диалога в логи не попадают.
"""

import asyncio
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

import httpx
from pydantic import SecretStr

from brain.llm import FunctionCall, JsonObject, LlmError, LlmMessage, LlmReply
from common.logging import getLogger
from contracts.agent_side import ToolSpec

RETRY_DELAY_S: Final = 1.0
TOKEN_REFRESH_MARGIN_S: Final = 60.0
_RATE_LIMITED: Final = 429
_UNAUTHORIZED: Final = 401
_SERVER_ERROR: Final = 500

_log = getLogger(__name__)


# Примечание: адрес, ключ и модели GigaChat (из LlmSettings)
@dataclass(frozen=True)
class GigaChatConfig:
    credentials: SecretStr
    scope: str
    authUrl: str
    apiUrl: str
    model: str
    fallbackModel: str | None


# Примечание: токен доступа и момент, после которого его пора обновить (time.monotonic)
@dataclass(frozen=True)
class _Token:
    value: str
    refreshAt: float


# Примечание: адаптер GigaChat; http-клиент (таймаут, сертификат) создаёт и закрывает агент
class GigaChatClient:
    def __init__(
        self, http: httpx.AsyncClient, config: GigaChatConfig, retryDelayS: float = RETRY_DELAY_S
    ) -> None:
        self._http = http
        self._config = config
        self._retryDelayS = retryDelayS
        self._token: _Token | None = None

    # Примечание: основная модель с одним повтором; не вышло — запасная (если задана)
    async def complete(
        self,
        messages: Sequence[LlmMessage],
        tools: Sequence[ToolSpec],
        temperature: float = 0.0,
    ) -> LlmReply:
        body: JsonObject = {
            "messages": [_message(m) for m in messages],
            "temperature": temperature,
        }
        if tools:
            body["functions"] = [toFunction(t) for t in tools]
            body["function_call"] = "auto"
        try:
            return await self._withRetry(self._config.model, body)
        except LlmError as exc:
            fallback = self._config.fallbackModel
            if fallback is None or exc.code == "llm_bad_output":
                raise
            _log.warning("llm_fallback_model", code=exc.code, model=fallback)
            return await self._call(fallback, body)

    # Примечание: одна попытка и один повтор на 429, 5xx и таймаут
    async def _withRetry(self, model: str, body: JsonObject) -> LlmReply:
        try:
            return await self._call(model, body)
        except LlmError as exc:
            if exc.code == "llm_bad_output":
                raise
            _log.warning("llm_retry", code=exc.code, model=model)
            await asyncio.sleep(self._retryDelayS)
            return await self._call(model, body)

    # Примечание: один POST chat/completions; 401 — токен протух раньше срока: берём новый
    async def _call(self, model: str, body: JsonObject) -> LlmReply:
        started = time.perf_counter()
        response = await self._post(model, body, await self._accessToken())
        if response.status_code == _UNAUTHORIZED:
            self._token = None
            response = await self._post(model, body, await self._accessToken())
        _raiseForStatus(response)
        return _reply(response, model, int((time.perf_counter() - started) * 1000))

    async def _post(self, model: str, body: JsonObject, token: str) -> httpx.Response:
        try:
            return await self._http.post(
                f"{self._config.apiUrl}/chat/completions",
                json={**body, "model": model},
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.TimeoutException as exc:
            raise LlmError("llm_timeout", "GigaChat не ответил вовремя") from exc
        except httpx.TransportError as exc:
            raise LlmError("llm_timeout", "GigaChat недоступен") from exc

    # Примечание: токен из кеша процесса; за минуту до конца срока — новый
    async def _accessToken(self) -> str:
        if self._token is not None and time.monotonic() < self._token.refreshAt:
            return self._token.value
        try:
            response = await self._http.post(
                self._config.authUrl,
                data={"scope": self._config.scope},
                headers={
                    "Authorization": f"Basic {self._config.credentials.get_secret_value()}",
                    "RqUID": str(uuid.uuid4()),
                    "Accept": "application/json",
                },
            )
        except httpx.TimeoutException as exc:
            raise LlmError("llm_timeout", "GigaChat: токен не выдан вовремя") from exc
        except httpx.TransportError as exc:
            raise LlmError("llm_timeout", "GigaChat: сервис токенов недоступен") from exc
        _raiseForStatus(response)
        payload = _json(response)
        value, expiresAtMs = payload.get("access_token"), payload.get("expires_at")
        if not isinstance(value, str) or not isinstance(expiresAtMs, int):
            raise LlmError("llm_bad_output", "GigaChat: ответ без access_token")
        lifetimeS = expiresAtMs / 1000 - time.time()
        self._token = _Token(value, time.monotonic() + lifetimeS - TOKEN_REFRESH_MARGIN_S)
        return value


# Примечание: инструмент J3 → функция GigaChat; схема без $ref и anyOf, которые модель не любит
def toFunction(tool: ToolSpec) -> JsonObject:
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": simplifySchema(tool.inputSchema),
    }


# Примечание: $ref раскрыт по $defs, «X или null» → X (поле и так необязательное),
# несколько строковых вариантов (дата или дата-время) → строка с перечнем форматов
def simplifySchema(schema: Mapping[str, object]) -> JsonObject:
    defs = schema.get("$defs")
    definitions = defs if isinstance(defs, dict) else {}
    result = _simplify(schema, definitions)
    if not isinstance(result, dict):
        msg = "схема инструмента — не объект"
        raise TypeError(msg)
    result.pop("$defs", None)
    return result


def _simplify(node: object, defs: Mapping[str, object]) -> object:
    if isinstance(node, list):
        return [_simplify(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str):
        return _simplify(defs.get(ref.removeprefix("#/$defs/"), {}), defs)
    variants = node.get("anyOf")
    if isinstance(variants, list):
        return _merged(node, [v for v in variants if v != {"type": "null"}], defs)
    return {k: _simplify(v, defs) for k, v in node.items() if k not in {"title", "$defs"}}


# Примечание: anyOf без null: один вариант — он сам; несколько строковых — строка с форматами
def _merged(
    node: Mapping[str, object], variants: list[object], defs: Mapping[str, object]
) -> object:
    rest = {k: _simplify(v, defs) for k, v in node.items() if k not in {"anyOf", "title"}}
    simplified = [_simplify(v, defs) for v in variants]
    if len(simplified) == 1 and isinstance(simplified[0], dict):
        return {**simplified[0], **rest}
    formats = [v.get("format") for v in simplified if isinstance(v, dict)]
    if all(isinstance(v, dict) and v.get("type") == "string" for v in simplified):
        hint = " или ".join(str(f) for f in formats if f)
        description = str(rest.pop("description", "")).strip()
        joined = f"{description} (формат: {hint})" if description else f"формат: {hint}"
        return {**rest, "type": "string", "description": joined.strip()}
    return {**rest, "anyOf": simplified}


# Примечание: наше сообщение → сообщение GigaChat; результат инструмента — роль function
def _message(message: LlmMessage) -> JsonObject:
    wire: JsonObject = {"role": message.role, "content": message.content}
    if message.functionCall is not None:
        wire["function_call"] = {
            "name": message.functionCall.name,
            "arguments": message.functionCall.arguments,
        }
    if message.name is not None:
        wire["name"] = message.name
    return wire


# Примечание: коды HTTP → коды ошибок Brain; 4xx кроме 429 — ошибка запроса, повтор не поможет
def _raiseForStatus(response: httpx.Response) -> None:
    status = response.status_code
    if status < httpx.codes.BAD_REQUEST:
        return
    if status == _RATE_LIMITED:
        raise LlmError("llm_rate_limited", "GigaChat: превышен лимит запросов")
    if status >= _SERVER_ERROR:
        raise LlmError("llm_timeout", f"GigaChat: ошибка сервера {status}")
    raise LlmError("llm_bad_output", f"GigaChat отклонил запрос: {status}")


def _json(response: httpx.Response) -> dict[str, object]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise LlmError("llm_bad_output", "GigaChat: ответ не JSON") from exc
    if not isinstance(payload, dict):
        raise LlmError("llm_bad_output", "GigaChat: ответ не объект")
    return payload


# Примечание: choices[0].message → LlmReply; usage — токены запроса и ответа
def _reply(response: httpx.Response, model: str, latencyMs: int) -> LlmReply:
    payload = _json(response)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise LlmError("llm_bad_output", "GigaChat: ответ без choices")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise LlmError("llm_bad_output", "GigaChat: ответ без message")
    usage = payload.get("usage")
    tokens = usage if isinstance(usage, dict) else {}
    content = message.get("content")
    return LlmReply(
        text=content if isinstance(content, str) else "",
        functionCall=_functionCall(message.get("function_call")),
        model=str(payload.get("model") or model),
        tokensIn=_int(tokens.get("prompt_tokens")),
        tokensOut=_int(tokens.get("completion_tokens")),
        latencyMs=latencyMs,
    )


def _functionCall(raw: object) -> FunctionCall | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise LlmError("llm_bad_output", "GigaChat: function_call не объект")
    name, arguments = raw.get("name"), raw.get("arguments")
    if not isinstance(name, str) or not isinstance(arguments, dict):
        raise LlmError("llm_bad_output", "GigaChat: function_call без имени или аргументов")
    return FunctionCall(name=name, arguments={str(k): v for k, v in arguments.items()})


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0
