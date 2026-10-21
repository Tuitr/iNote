import dataclasses

from services.bot.tests.fakes import loadIncoming

from bot.application.handle_update import HandleOutcome, HandleUpdate
from bot.domain.updates import UpdateKind
from bot.infrastructure.fake_max import FakeMaxGateway
from bot.presentation import templates

CHAT_ID = 500600700


# Примечание: текст → эхо в тот же чат, формат plain
async def test_textIsEchoed(handler: HandleUpdate, gateway: FakeMaxGateway) -> None:
    outcome = await handler(loadIncoming("text"))
    assert outcome is HandleOutcome.HANDLED
    [sent] = gateway.sentMessages()
    assert sent.chatId == CHAT_ID
    assert sent.text == "Вы написали: Напомни завтра в 10 позвонить Ивану"
    assert sent.format == "plain"


# Примечание: команда → эхо имени команды
async def test_commandIsEchoed(handler: HandleUpdate, gateway: FakeMaxGateway) -> None:
    await handler(loadIncoming("command"))
    assert gateway.sentMessages()[0].text == templates.COMMAND.format(name="today")


# Примечание: голосовое → заглушка до J1
async def test_voiceGetsStub(handler: HandleUpdate, gateway: FakeMaxGateway) -> None:
    await handler(loadIncoming("voice"))
    assert gateway.sentMessages()[0].text == templates.VOICE_NOT_YET


# Примечание: кнопка → ответ на callback, нового сообщения нет
async def test_callbackAnswered(handler: HandleUpdate, gateway: FakeMaxGateway) -> None:
    await handler(loadIncoming("callback"))
    assert gateway.sentMessages() == ()
    [answer] = gateway.callbackAnswers()
    assert answer.callbackId == "cb.synthetic.0001"
    assert answer.notification is not None
    assert answer.notification.startswith("Нажата кнопка: undo:")


# Примечание: «Начать» → приветствие
async def test_botStartedGreets(handler: HandleUpdate, gateway: FakeMaxGateway) -> None:
    await handler(loadIncoming("bot_started"))
    assert gateway.sentMessages()[0].text == templates.GREETING


# Примечание: повтор того же апдейта не обрабатывается и не тратит лимит
async def test_duplicateSkipped(handler: HandleUpdate, gateway: FakeMaxGateway) -> None:
    update = loadIncoming("text")
    assert await handler(update) is HandleOutcome.HANDLED
    assert await handler(update) is HandleOutcome.DUPLICATE
    assert len(gateway.sentMessages()) == 1


# Примечание: сверх лимита «Слишком часто» — один раз за окно; на кнопку — каждый раз
async def test_rateLimited(handler: HandleUpdate, gateway: FakeMaxGateway) -> None:
    base = loadIncoming("text")
    outcomes = [await handler(dataclasses.replace(base, updateId=f"msg-{i}")) for i in range(6)]
    assert outcomes[3:] == [HandleOutcome.RATE_LIMITED] * 3
    texts = [m.text for m in gateway.sentMessages()]
    assert texts.count(templates.TOO_OFTEN) == 1
    assert len(texts) == 4
    for i in range(2):
        callback = dataclasses.replace(
            loadIncoming("callback"), maxUserId=base.maxUserId, updateId=f"cb-{i}"
        )
        assert await handler(callback) is HandleOutcome.RATE_LIMITED
    assert [a.notification for a in gateway.callbackAnswers()] == [templates.TOO_OFTEN] * 2


# Примечание: длинное эхо обрезается до лимита MAX, а не падает
async def test_longEchoFitsLimit(handler: HandleUpdate, gateway: FakeMaxGateway) -> None:
    update = dataclasses.replace(loadIncoming("text"), text="я" * 4000)
    await handler(update)
    sent = gateway.sentMessages()[0].text
    assert len(sent) == 4000
    assert sent.endswith("…")


# Примечание: апдейт без чата (callback к удалённому сообщению) — без ответа и без падения
async def test_noChat(handler: HandleUpdate, gateway: FakeMaxGateway) -> None:
    update = dataclasses.replace(loadIncoming("text"), maxChatId=None)
    assert await handler(update) is HandleOutcome.NO_CHAT
    assert gateway.sentMessages() == ()
    assert update.kind is UpdateKind.TEXT
