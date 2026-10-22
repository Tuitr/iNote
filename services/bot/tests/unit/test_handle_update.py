import dataclasses
from typing import Final

import pytest
import structlog

from bot.application.handle_update import HandleOutcome, HandleUpdate
from bot.application.ports import StoredAction
from bot.domain.updates import UpdateKind
from bot.domain.voice import VoiceFailureError, VoiceFailureReason
from bot.infrastructure.fake_max import FakeMaxGateway
from bot.presentation import templates
from common.errors import AppError, ErrorCode
from common.trace import traceContext
from contracts.reply import ActionRequest, Reply
from services.bot.tests.fakes import (
    FakeAgent,
    FakeAsr,
    FakeDownloader,
    InMemoryCallbackStore,
    exampleReply,
    loadIncoming,
)

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


# Примечание: пока агент думает — «печатает…» уходит сразу и повторяется; свой текст бота — без него
async def test_typingWhileAgentThinks(
    handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent
) -> None:
    agent.delayS = 0.1
    await handler(loadIncoming("text"))
    typing = gateway.typingActions()
    assert len(typing) >= 3
    assert set(typing) == {CHAT_ID}
    after = len(typing)
    await handler(dataclasses.replace(loadIncoming("command"), updateId="cmd-2", commandName="lol"))
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


# Примечание: кнопку нажали в другом чате (пересланное сообщение) — «Кнопка устарела», агента нет
async def test_foreignChatButtonRejected(
    handler: HandleUpdate,
    gateway: FakeMaxGateway,
    agent: FakeAgent,
    callbacks: InMemoryCallbackStore,
) -> None:
    key = await callbacks.save(StoredAction("undo", {"item_id": "7f3a9c2e"}, chatId=111))
    update = dataclasses.replace(loadIncoming("callback"), callbackPayload=key)
    assert await handler(update) is HandleOutcome.BUTTON_EXPIRED
    assert gateway.callbackAnswers()[0].notification == templates.BUTTON_EXPIRED
    assert agent.actions == []
    same = await callbacks.save(StoredAction("undo", {"item_id": "7f3a9c2e"}, chatId=CHAT_ID))
    pressed = dataclasses.replace(update, callbackPayload=same, updateId="cb-same")
    assert await handler(pressed) is HandleOutcome.HANDLED


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


# Примечание: все команды каталога → /v1/actions; @имя_бота отрезается, регистр не важен
@pytest.mark.parametrize(
    ("command", "action", "args"),
    [
        ("start", "start", {}),
        ("help", "help", {}),
        ("today", "list_period", {"period": "today"}),
        ("Tomorrow", "list_period", {"period": "tomorrow"}),
        ("week@va_bot", "list_period", {"period": "week"}),
    ],
)
async def test_commandsGoToActions(
    *,
    handler: HandleUpdate,
    gateway: FakeMaxGateway,
    agent: FakeAgent,
    command: str,
    action: str,
    args: dict[str, str],
) -> None:
    await handler(dataclasses.replace(loadIncoming("command"), commandName=command))
    [request] = agent.actions
    assert (request.action, request.args, request.callbackId) == (action, args, None)
    assert len(gateway.sentMessages()) == 1


# Примечание: незнакомая команда — подсказка бота, агент не вызывается
async def test_unknownCommand(
    handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent
) -> None:
    await handler(dataclasses.replace(loadIncoming("command"), commandName="lol"))
    assert [m.text for m in gateway.sentMessages()] == [
        templates.UNKNOWN_COMMAND.format(name="lol")
    ]
    assert agent.actions == []


# Примечание: «Начать» → действие start у агента (приветствие — его шаблон), ответ — в чат
async def test_botStarted(handler: HandleUpdate, gateway: FakeMaxGateway, agent: FakeAgent) -> None:
    await handler(loadIncoming("bot_started"))
    [request] = agent.actions
    assert request.action == "start"
    assert agent.messages == []
    assert [m.text for m in gateway.sentMessages()] == [exampleReply("confirm.json").text]


# Примечание: спиннер кнопки гаснет до вызова агента — даже если агент думает долго
async def test_callbackAnsweredBeforeAgent(
    handler: HandleUpdate,
    gateway: FakeMaxGateway,
    agent: FakeAgent,
    callbacks: InMemoryCallbackStore,
) -> None:
    key = await callbacks.save(StoredAction("list_period", {"period": "week"}))
    seen: list[int] = []
    original = agent.sendAction

    # Примечание: в момент вызова агента ответ на нажатие уже ушёл
    async def spy(request: ActionRequest) -> Reply:
        seen.append(len(gateway.callbackAnswers()))
        return await original(request)

    agent.sendAction = spy  # type: ignore[method-assign]
    await handler(dataclasses.replace(loadIncoming("callback"), callbackPayload=key))
    assert seen == [1]


# Примечание: голос: скачать → ASR → агенту распознанный текст с source=voice и флагами ASR
async def test_voiceGoesToAgent(
    handler: HandleUpdate,
    gateway: FakeMaxGateway,
    agent: FakeAgent,
    downloader: FakeDownloader,
    asr: FakeAsr,
) -> None:
    asr.low = True
    assert await handler(loadIncoming("voice")) is HandleOutcome.HANDLED
    assert downloader.urls == ["https://example.invalid/synthetic/voice-0001.ogg"]
    [request] = agent.messages
    assert (request.text, request.source) == ("Напомни завтра в 10 позвонить Ивану", "voice")
    assert (request.asrLowConfidence, request.asrDurationMs) == (True, 2100)
    assert (request.name, request.locale) == ("Иван", "ru")
    [sent] = gateway.sentMessages()
    assert sent.text == exampleReply("confirm.json").text


# Примечание: «печатает…» горит и пока ASR думает — а не только пока ждём агента
async def test_typingDuringAsr(
    handler: HandleUpdate, gateway: FakeMaxGateway, asr: FakeAsr
) -> None:
    asr.delayS = 0.1
    await handler(loadIncoming("voice"))
    assert len(gateway.typingActions()) >= 3


# Примечание: каждая причина сбоя голосового — свой текст бота с кодом; агент не вызывается
@pytest.mark.parametrize(
    ("reason", "template"),
    [
        (VoiceFailureReason.NO_SPEECH, templates.VOICE_NO_SPEECH),
        (VoiceFailureReason.TOO_LONG, templates.VOICE_TOO_LONG),
        (VoiceFailureReason.UNSUPPORTED, templates.VOICE_FAILED),
        (VoiceFailureReason.UNAVAILABLE, templates.VOICE_FAILED),
    ],
)
async def test_voiceFailures(
    *,
    handler: HandleUpdate,
    gateway: FakeMaxGateway,
    agent: FakeAgent,
    asr: FakeAsr,
    reason: VoiceFailureReason,
    template: str,
) -> None:
    asr.error = VoiceFailureError(reason, "x")
    with traceContext(TRACE):
        assert await handler(loadIncoming("voice")) is HandleOutcome.VOICE_FAILED
    [sent] = gateway.sentMessages()
    assert sent.text == template.format(code="ab12f0")
    assert agent.messages == []


# Примечание: голосовое больше лимита — тот же ответ, что «слишком длинное», ASR не вызывается
async def test_voiceTooLarge(
    handler: HandleUpdate, gateway: FakeMaxGateway, downloader: FakeDownloader, asr: FakeAsr
) -> None:
    downloader.error = VoiceFailureError(VoiceFailureReason.TOO_LONG, "voice_too_large")
    await handler(loadIncoming("voice"))
    assert gateway.sentMessages()[0].text.startswith("Слишком длинное голосовое")
    assert asr.calls == []


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
