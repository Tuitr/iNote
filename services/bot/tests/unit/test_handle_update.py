import dataclasses
from typing import Final

import structlog

from bot.application.handle_update import HandleOutcome, HandleUpdate
from bot.application.ports import StoredAction
from bot.domain.updates import UpdateKind
from bot.infrastructure.fake_max import FakeMaxGateway
from bot.presentation import templates
from common.errors import AppError, ErrorCode
from common.trace import traceContext
from services.bot.tests.fakes import FakeAgent, InMemoryCallbackStore, exampleReply, loadIncoming

CHAT_ID: Final = 500600700
TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"


# Примечание: текст → агент (имя и язык для J8) → Reply в формате MAX: markdown и клавиатура
async def test_textGoesToAgent(
    handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent
) -> None:
    assert await handler(loadIncoming("text")) is HandleOutcome.HANDLED
    [request] = agent.messages
    assert (request.maxUserId, request.maxChatId) == (100200300, CHAT_ID)
    assert (request.text, request.source) == ("Напомни завтра в 10 позвонить Ивану", "text")
    assert (request.name, request.locale) == ("Иван", "ru")
    [sent] = gateway.sentMessages()
    reply = exampleReply("confirm.json")
    assert (sent.chatId, sent.text, sent.format) == (CHAT_ID, reply.text, "markdown")
    [keyboard] = sent.attachments
    assert keyboard["type"] == "inline_keyboard"


# Примечание: пока агент думает — «печатает…» уходит сразу и повторяется; после ответа — тишина
async def test_typingWhileAgentThinks(
    handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent
) -> None:
    agent.delayS = 0.1
    await handler(loadIncoming("text"))
    typing = gateway.typingActions()
    assert len(typing) >= 3
    assert set(typing) == {CHAT_ID}
    after = len(typing)
    await handler(dataclasses.replace(loadIncoming("bot_started"), updateId="start-2"))
    assert len(gateway.typingActions()) == after


# Примечание: агент недоступен — пользователь видит текст бота с кодом цепочки, а не тишину
async def test_agentFailure(
    handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent
) -> None:
    agent.error = AppError(ErrorCode.UPSTREAM_TIMEOUT, "agent не ответил вовремя")
    with traceContext(TRACE), structlog.testing.capture_logs() as logs:
        outcome = await handler(loadIncoming("text"))
    assert outcome is HandleOutcome.AGENT_FAILED
    [sent] = gateway.sentMessages()
    assert sent.text == templates.AGENT_FAILED.format(code="ab12f0")
    assert sent.format == "plain"
    assert any(e["event"] == "agent_failed" and e["error_code"] == "upstream_timeout" for e in logs)


# Примечание: неожиданное исключение — тот же текст с кодом, исключение в лог
async def test_agentCrash(handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent) -> None:
    agent.error = RuntimeError("boom")
    assert await handler(loadIncoming("text")) is HandleOutcome.AGENT_FAILED
    assert gateway.sentMessages()[0].text.startswith("Не получилось связаться")


# Примечание: кнопка: спиннер гаснет сразу, действие из хранилища уходит в /v1/actions
async def test_callbackGoesToAgent(
    handler: HandleUpdate,
    gateway: FakeMaxGateway,
    agent: FakeAgent,
    callbacks: InMemoryCallbackStore,
) -> None:
    key = await callbacks.save(StoredAction("undo", {"item_id": "7f3a9c2e"}))
    update = dataclasses.replace(loadIncoming("callback"), callbackPayload=key)
    assert await handler(update) is HandleOutcome.HANDLED
    [answer] = gateway.callbackAnswers()
    assert (answer.callbackId, answer.notification) == ("cb.synthetic.0001", None)
    [request] = agent.actions
    assert (request.action, request.args) == ("undo", {"item_id": "7f3a9c2e"})
    assert request.callbackId == "cb.synthetic.0001"
    assert len(gateway.sentMessages()) == 1


# Примечание: payload не из хранилища (старый, истёк, подделка) — «Кнопка устарела», агента нет
async def test_expiredButton(
    handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent
) -> None:
    assert await handler(loadIncoming("callback")) is HandleOutcome.BUTTON_EXPIRED
    [answer] = gateway.callbackAnswers()
    assert answer.notification == templates.BUTTON_EXPIRED
    assert agent.actions == []
    assert gateway.sentMessages() == ()


# Примечание: /today → действие list_period без LLM
async def test_commandGoesToActions(
    handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent
) -> None:
    await handler(loadIncoming("command"))
    [request] = agent.actions
    assert (request.action, request.args) == ("list_period", {"period": "today"})
    assert len(gateway.sentMessages()) == 1


# Примечание: /help и незнакомая команда — тексты бота, агент не вызывается
async def test_localCommands(
    handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent
) -> None:
    base = loadIncoming("command")
    await handler(dataclasses.replace(base, updateId="c-1", commandName="help"))
    await handler(dataclasses.replace(base, updateId="c-2", commandName="lol"))
    texts = [m.text for m in gateway.sentMessages()]
    assert texts == [templates.HELP, templates.UNKNOWN_COMMAND.format(name="lol")]
    assert agent.actions == []


# Примечание: голосовое → заглушка до J1; «Начать» → приветствие; агент не нужен
async def test_voiceAndStart(
    handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent
) -> None:
    await handler(loadIncoming("voice"))
    await handler(loadIncoming("bot_started"))
    assert [m.text for m in gateway.sentMessages()] == [templates.VOICE_NOT_YET, templates.GREETING]
    assert agent.messages == []


# Примечание: повтор того же апдейта не обрабатывается и не тратит лимит
async def test_duplicateSkipped(handler: HandleUpdate, agent: FakeAgent) -> None:
    update = loadIncoming("text")
    assert await handler(update) is HandleOutcome.HANDLED
    assert await handler(update) is HandleOutcome.DUPLICATE
    assert len(agent.messages) == 1


# Примечание: сверх лимита «Слишком часто» — один раз за окно; на кнопку — каждый раз
async def test_rateLimited(
    handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent
) -> None:
    base = loadIncoming("text")
    outcomes = [await handler(dataclasses.replace(base, updateId=f"msg-{i}")) for i in range(6)]
    assert outcomes[3:] == [HandleOutcome.RATE_LIMITED] * 3
    texts = [m.text for m in gateway.sentMessages()]
    assert texts.count(templates.TOO_OFTEN) == 1
    assert len(agent.messages) == 3
    for i in range(2):
        callback = dataclasses.replace(
            loadIncoming("callback"), maxUserId=base.maxUserId, updateId=f"cb-{i}"
        )
        assert await handler(callback) is HandleOutcome.RATE_LIMITED
    assert [a.notification for a in gateway.callbackAnswers()] == [templates.TOO_OFTEN] * 2


# Примечание: длинный текст пользователя обрезается до лимита запроса, а не падает
async def test_longTextFitsLimit(handler: HandleUpdate, agent: FakeAgent) -> None:
    await handler(dataclasses.replace(loadIncoming("text"), text="я" * 5000))
    assert len(agent.messages[0].text) == 4000


# Примечание: апдейт без чата — без ответа и без падения
async def test_noChat(handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent) -> None:
    update = dataclasses.replace(loadIncoming("text"), maxChatId=None)
    assert await handler(update) is HandleOutcome.NO_CHAT
    assert gateway.sentMessages() == ()
    assert agent.messages == []
    assert update.kind is UpdateKind.TEXT


# Примечание: в логах бота нет текста пользователя (правило 10)
async def test_logsWithoutUserText(handler: HandleUpdate) -> None:
    with structlog.testing.capture_logs() as logs:
        await handler(loadIncoming("text"))
    assert "позвонить" not in str(logs).lower()
