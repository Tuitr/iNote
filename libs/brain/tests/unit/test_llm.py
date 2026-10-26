import pytest

from brain.llm import FakeLlmClient, FunctionCall, LlmError, LlmMessage, LlmReply

MESSAGES = [LlmMessage(role="user", content="что у меня завтра")]


# Примечание: фейк отдаёт ответы сценария по очереди и запоминает, что ему передали
async def test_fakeReplaysScript() -> None:
    call = FunctionCall(name="list_items", arguments={"from": "2026-10-25"})
    fake = FakeLlmClient(
        script=[
            LlmReply(text="", functionCall=call, model="fake"),
            LlmReply(text="Готово", functionCall=None, model="fake"),
        ]
    )
    first = await fake.complete(MESSAGES, [])
    second = await fake.complete(MESSAGES, [], temperature=0.0)
    assert (first.functionCall, second.text) == (call, "Готово")
    assert [c.messages for c in fake.calls] == [tuple(MESSAGES), tuple(MESSAGES)]


# Примечание: ошибка в сценарии выбрасывается; кончились ответы — llm_bad_output, а не зависание
async def test_fakeErrors() -> None:
    fake = FakeLlmClient(script=[LlmError("llm_timeout", "slow")])
    with pytest.raises(LlmError) as timeout:
        await fake.complete(MESSAGES, [])
    with pytest.raises(LlmError) as empty:
        await fake.complete(MESSAGES, [])
    assert (timeout.value.code, empty.value.code) == ("llm_timeout", "llm_bad_output")
