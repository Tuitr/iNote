import asyncio

from services.bot.tests.fakes import loadMaxUpdate

from bot.domain.messages import OutgoingMessage
from bot.infrastructure.fake_max import FakeMaxGateway


# Примечание: пустая очередь — ждём до таймаута и возвращаем тот же marker
async def test_fetchTimesOutEmpty(gateway: FakeMaxGateway) -> None:
    batch = await gateway.fetchUpdates(marker=None, timeoutS=0)
    assert batch.updates == ()
    assert batch.marker == 0


# Примечание: long polling просыпается, как только подложили апдейт
async def test_fetchWakesOnPush(gateway: FakeMaxGateway) -> None:
    fetch = asyncio.create_task(gateway.fetchUpdates(marker=None, timeoutS=5))
    await asyncio.sleep(0.01)
    await gateway.pushRaw(loadMaxUpdate("text"))
    batch = await asyncio.wait_for(fetch, timeout=1)
    assert len(batch.updates) == 1
    assert batch.marker == 1


# Примечание: marker=None отдаёт только новое; старый marker — повторная выдача (как в MAX)
async def test_markerSemantics(gateway: FakeMaxGateway) -> None:
    await gateway.pushRaw(loadMaxUpdate("text"))
    first = await gateway.fetchUpdates(marker=None, timeoutS=0)
    await gateway.pushRaw(loadMaxUpdate("command"))
    fresh = await gateway.fetchUpdates(marker=None, timeoutS=0)
    replay = await gateway.fetchUpdates(marker=0, timeoutS=0)
    assert [u.kind.value for u in first.updates] == ["text"]
    assert [u.kind.value for u in fresh.updates] == ["command"]
    assert [u.kind.value for u in replay.updates] == ["text", "command"]


# Примечание: неподдержанный апдейт двигает marker, но в страницу не попадает
async def test_unsupportedSkipped(gateway: FakeMaxGateway) -> None:
    await gateway.pushRaw({"update_type": "dialog_muted", "timestamp": 1})
    batch = await gateway.fetchUpdates(marker=None, timeoutS=0)
    assert batch.updates == ()
    assert batch.marker == 1


# Примечание: журнал отправленного
async def test_sentJournal(gateway: FakeMaxGateway) -> None:
    messageId = await gateway.sendMessage(OutgoingMessage(chatId=1, text="привет"))
    await gateway.answerCallback("cb-1", "ок")
    assert gateway.sentMessages()[0].messageId == messageId
    assert gateway.callbackAnswers()[0].notification == "ок"
