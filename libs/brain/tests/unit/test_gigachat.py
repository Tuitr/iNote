"""GigaChatClient на фейковом транспорте (respx): без сети, ключей и денег."""

import json
import time
from collections.abc import AsyncIterator
from typing import Final

import httpx
import pytest
import respx
from pydantic import SecretStr

from brain.gigachat import GigaChatClient, GigaChatConfig, simplifySchema, toFunction
from brain.llm import FunctionCall, LlmError, LlmMessage
from contracts.agent_side import ToolSpec

AUTH_URL: Final = "https://auth.test/api/v2/oauth"
API_URL: Final = "https://api.test/api/v1"
CHAT_URL: Final = f"{API_URL}/chat/completions"
CONFIG: Final = GigaChatConfig(
    credentials=SecretStr("base64-key"),
    scope="GIGACHAT_API_PERS",
    authUrl=AUTH_URL,
    apiUrl=API_URL,
    model="GigaChat-2-Max",
    fallbackModel="GigaChat-2-Pro",
)
MESSAGES: Final = [LlmMessage(role="user", content="Напомни завтра в 10 позвонить Ивану")]
TOOL: Final = ToolSpec(
    name="create_task",
    description="Создать задачу или напоминание",
    inputSchema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "title": "Title"},
            "remind_at": {
                "anyOf": [{"type": "string", "format": "date-time"}, {"type": "null"}],
                "default": None,
                "title": "Remind At",
            },
        },
        "required": ["title"],
    },
)


def tokenResponse() -> httpx.Response:
    expiresAtMs = int((time.time() + 1800) * 1000)
    return httpx.Response(200, json={"access_token": "tok", "expires_at": expiresAtMs})


def functionReply(model: str = "GigaChat-2-Max") -> httpx.Response:
    message = {
        "role": "assistant",
        "content": "",
        "function_call": {
            "name": "create_task",
            "arguments": {"title": "Позвонить Ивану", "remind_at": "2026-10-25T10:00:00+03:00"},
        },
        "functions_state_id": "state-1",
    }
    return httpx.Response(
        200,
        json={
            "choices": [{"message": message, "finish_reason": "function_call"}],
            "model": model,
            "usage": {"prompt_tokens": 120, "completion_tokens": 30},
        },
    )


def textReply(text: str) -> httpx.Response:
    message = {"role": "assistant", "content": text}
    return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": "stop"}]})


@pytest.fixture
async def client() -> AsyncIterator[GigaChatClient]:
    async with httpx.AsyncClient() as http:
        yield GigaChatClient(http, CONFIG, retryDelayS=0)


# Примечание: вызов инструмента: function_call с объектом аргументов, токены и модель из ответа
@respx.mock
async def test_functionCallParsed(client: GigaChatClient) -> None:
    auth = respx.post(AUTH_URL).mock(return_value=tokenResponse())
    chat = respx.post(CHAT_URL).mock(return_value=functionReply())
    reply = await client.complete(MESSAGES, [TOOL])
    assert reply.functionCall == FunctionCall(
        name="create_task",
        arguments={"title": "Позвонить Ивану", "remind_at": "2026-10-25T10:00:00+03:00"},
    )
    assert (reply.model, reply.tokensIn, reply.tokensOut) == ("GigaChat-2-Max", 120, 30)
    assert reply.stateId == "state-1"
    tokenRequest = auth.calls.last.request
    assert tokenRequest.headers["Authorization"] == "Basic base64-key"
    assert "RqUID" in tokenRequest.headers
    assert tokenRequest.content == b"scope=GIGACHAT_API_PERS"
    body = json.loads(chat.calls.last.request.content)
    assert chat.calls.last.request.headers["Authorization"] == "Bearer tok"
    assert (body["model"], body["temperature"], body["function_call"]) == (
        "GigaChat-2-Max",
        0.0,
        "auto",
    )
    assert body["functions"][0]["parameters"]["properties"]["remind_at"] == {
        "type": "string",
        "format": "date-time",
        "default": None,
    }


# Примечание: токен живёт 30 мин — второй вызов идёт без нового запроса токена
@respx.mock
async def test_tokenReused(client: GigaChatClient) -> None:
    auth = respx.post(AUTH_URL).mock(return_value=tokenResponse())
    respx.post(CHAT_URL).mock(return_value=textReply("Готово"))
    await client.complete(MESSAGES, [])
    reply = await client.complete(MESSAGES, [])
    assert reply.text == "Готово"
    assert reply.functionCall is None
    assert auth.call_count == 1


# Примечание: без инструментов functions не уходят; результат инструмента — роль function с name
@respx.mock
async def test_messagesOnWire(client: GigaChatClient) -> None:
    respx.post(AUTH_URL).mock(return_value=tokenResponse())
    chat = respx.post(CHAT_URL).mock(return_value=textReply("ok"))
    call = FunctionCall(name="list_items", arguments={"from": "2026-10-25"})
    await client.complete(
        [
            LlmMessage(role="assistant", content="", functionCall=call, stateId="state-1"),
            LlmMessage(role="function", content='{"items": []}', name="list_items"),
        ],
        [],
    )
    body = json.loads(chat.calls.last.request.content)
    assert "functions" not in body
    assert body["messages"] == [
        {
            "role": "assistant",
            "content": "",
            "function_call": {"name": "list_items", "arguments": {"from": "2026-10-25"}},
            "functions_state_id": "state-1",
        },
        {"role": "function", "content": '{"items": []}', "name": "list_items"},
    ]


# Примечание: 503, затем успех — один повтор той же модели
@respx.mock
async def test_retryOnServerError(client: GigaChatClient) -> None:
    respx.post(AUTH_URL).mock(return_value=tokenResponse())
    chat = respx.post(CHAT_URL).mock(side_effect=[httpx.Response(503), functionReply()])
    reply = await client.complete(MESSAGES, [TOOL])
    assert reply.functionCall is not None
    assert [json.loads(c.request.content)["model"] for c in chat.calls] == [
        "GigaChat-2-Max",
        "GigaChat-2-Max",
    ]


# Примечание: основная модель дважды не ответила вовремя — одна попытка запасной
@respx.mock
async def test_fallbackModelAfterTimeouts(client: GigaChatClient) -> None:
    respx.post(AUTH_URL).mock(return_value=tokenResponse())
    chat = respx.post(CHAT_URL).mock(
        side_effect=[
            httpx.ReadTimeout("slow"),
            httpx.ReadTimeout("slow"),
            functionReply("GigaChat-2-Pro"),
        ]
    )
    reply = await client.complete(MESSAGES, [TOOL])
    assert reply.model == "GigaChat-2-Pro"
    assert [json.loads(c.request.content)["model"] for c in chat.calls] == [
        "GigaChat-2-Max",
        "GigaChat-2-Max",
        "GigaChat-2-Pro",
    ]


# Примечание: 429 у всех — llm_rate_limited; без запасной модели — сразу после повтора
@respx.mock
async def test_rateLimited() -> None:
    respx.post(AUTH_URL).mock(return_value=tokenResponse())
    chat = respx.post(CHAT_URL).mock(return_value=httpx.Response(429))
    config = GigaChatConfig(**{**CONFIG.__dict__, "fallbackModel": None})
    async with httpx.AsyncClient() as http:
        with pytest.raises(LlmError) as caught:
            await GigaChatClient(http, config, retryDelayS=0).complete(MESSAGES, [TOOL])
    assert caught.value.code == "llm_rate_limited"
    assert chat.call_count == 2


# Примечание: непонятный ответ — llm_bad_output без повторов (повтор того же не исправит)
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "function_call": {"name": 1}}}]},
        ),
        httpx.Response(400, json={"message": "bad"}),
    ],
)
@respx.mock
async def test_badOutputNoRetry(client: GigaChatClient, response: httpx.Response) -> None:
    respx.post(AUTH_URL).mock(return_value=tokenResponse())
    chat = respx.post(CHAT_URL).mock(return_value=response)
    with pytest.raises(LlmError) as caught:
        await client.complete(MESSAGES, [TOOL])
    assert caught.value.code == "llm_bad_output"
    assert chat.call_count == 1


# Примечание: 401 на вызове — токен протух раньше срока: новый токен и тот же запрос
@respx.mock
async def test_unauthorizedRefreshesToken(client: GigaChatClient) -> None:
    auth = respx.post(AUTH_URL).mock(return_value=tokenResponse())
    respx.post(CHAT_URL).mock(side_effect=[httpx.Response(401), textReply("ok")])
    reply = await client.complete(MESSAGES, [])
    assert reply.text == "ok"
    assert auth.call_count == 2


# Примечание: схема инструмента D без $ref и anyOf: дата или дата-время — строка с форматами
def test_simplifySchema() -> None:
    schema = {
        "$defs": {
            "ItemKind": {
                "enum": ["task", "reminder", "event"],
                "title": "ItemKind",
                "type": "string",
            }
        },
        "type": "object",
        "title": "SearchItemsArgs",
        "properties": {
            "kind": {
                "anyOf": [
                    {"items": {"$ref": "#/$defs/ItemKind"}, "type": "array"},
                    {"type": "null"},
                ],
                "title": "Kind",
            },
            "starts_at": {
                "anyOf": [
                    {"type": "string", "format": "date-time"},
                    {"type": "string", "format": "date"},
                    {"type": "null"},
                ],
                "description": "Начало",
            },
        },
    }
    assert simplifySchema(schema) == {
        "type": "object",
        "properties": {
            "kind": {
                "type": "array",
                "items": {"enum": ["task", "reminder", "event"], "type": "string"},
            },
            "starts_at": {"type": "string", "description": "Начало (формат: date-time или date)"},
        },
    }
    assert toFunction(TOOL)["name"] == "create_task"


# Примечание: поле «title» — имя поля, а не ключ схемы: без него модель не передаёт название
def test_simplifySchemaKeepsTitleField() -> None:
    parameters = toFunction(TOOL)["parameters"]
    assert isinstance(parameters, dict)
    assert parameters["properties"]["title"] == {"type": "string"}
