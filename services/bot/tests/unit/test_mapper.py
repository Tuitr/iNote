import copy
from typing import Final

import pytest

from bot.domain.updates import UpdateKind, VoiceRef
from bot.infrastructure.max_mapper import deriveUpdateId, mapRawUpdate
from services.bot.tests.fakes import FIXTURE_NAMES, loadMaxUpdate

USER_ID: Final = 100200300
CHAT_ID: Final = 500600700


# Примечание: все фикстуры разбираются и помечены как синтетика
@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_allFixturesMapAndAreMarkedSynthetic(name: str) -> None:
    raw = loadMaxUpdate(name)
    assert raw["_synthetic"] is True
    update = mapRawUpdate(raw)
    assert update is not None
    assert update.maxUserId == USER_ID


# Примечание: вид апдейта по каждой фикстуре
@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("text", UpdateKind.TEXT),
        ("command", UpdateKind.COMMAND),
        ("voice", UpdateKind.VOICE),
        ("callback", UpdateKind.CALLBACK),
        ("bot_started", UpdateKind.BOT_STARTED),
    ],
)
def test_kinds(name: str, kind: UpdateKind) -> None:
    update = mapRawUpdate(loadMaxUpdate(name))
    assert update is not None
    assert update.kind is kind


# Примечание: поля текстового апдейта
def test_textFields() -> None:
    update = mapRawUpdate(loadMaxUpdate("text"))
    assert update is not None
    assert update.maxChatId == CHAT_ID
    assert update.text == "Напомни завтра в 10 позвонить Ивану"
    assert update.updateId == deriveUpdateId("msg", "mid.synthetic.text.0001")


# Примечание: команда — имя без «/»
def test_commandName() -> None:
    update = mapRawUpdate(loadMaxUpdate("command"))
    assert update is not None
    assert update.commandName == "today"


# Примечание: голосовое — ссылка и токен; длительности и mime в схеме нет
def test_voiceRef() -> None:
    update = mapRawUpdate(loadMaxUpdate("voice"))
    assert update is not None
    assert update.voice == VoiceRef(
        url="https://example.invalid/synthetic/voice-0001.ogg", token="synthetic-audio-token-0001"
    )


# Примечание: callback — id, payload и чат исходного сообщения
def test_callbackFields() -> None:
    update = mapRawUpdate(loadMaxUpdate("callback"))
    assert update is not None
    assert update.callbackId == "cb.synthetic.0001"
    assert update.callbackPayload == "undo:7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90"
    assert update.maxChatId == CHAT_ID


# Примечание: callback к удалённому сообщению — чата нет, но апдейт валиден
def test_callbackWithoutMessage() -> None:
    raw = loadMaxUpdate("callback")
    raw["message"] = None
    update = mapRawUpdate(raw)
    assert update is not None
    assert update.maxChatId is None


# Примечание: живой MAX кладёт в нажатие сообщение бота с клавиатурой — у неё payload без url
def test_callbackWithKeyboardMessage() -> None:
    raw = loadMaxUpdate("callback")
    message = raw["message"]
    assert isinstance(message, dict)
    keyboard = {"type": "inline_keyboard", "payload": {"buttons": [[{"type": "callback"}]]}}
    message["body"] = {**message["body"], "attachments": [keyboard]}
    update = mapRawUpdate(raw)
    assert update is not None
    assert update.callbackPayload == "undo:7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90"


# Примечание: «Начать» — payload из deep link
def test_botStartedPayload() -> None:
    update = mapRawUpdate(loadMaxUpdate("bot_started"))
    assert update is not None
    assert (update.startPayload, update.maxChatId) == ("today", CHAT_ID)


# Примечание: id стабилен для одного события и различается для разных
def test_updateIdStableAndDistinct() -> None:
    first = mapRawUpdate(loadMaxUpdate("text"))
    again = mapRawUpdate(loadMaxUpdate("text"))
    other = mapRawUpdate(loadMaxUpdate("command"))
    assert first is not None
    assert again is not None
    assert other is not None
    assert first.updateId == again.updateId != other.updateId
    # id годится для ключа Redis: только [A-Za-z0-9_.-]
    assert first.updateId.replace("-", "").isalnum()


# Примечание: повторное нажатие той же клавиатуры — другой id (время нажатия в ключе)
def test_repeatedCallbackPressIsNotDuplicate() -> None:
    raw = loadMaxUpdate("callback")
    later = copy.deepcopy(raw)
    callback = later["callback"]
    assert isinstance(callback, dict)
    callback["timestamp"] = 1792580590000
    a, b = mapRawUpdate(raw), mapRawUpdate(later)
    assert a is not None
    assert b is not None
    assert a.updateId != b.updateId


# Примечание: что ассистенту не нужно — игнорируется без исключения
@pytest.mark.parametrize(
    "mutate",
    ["unsupported", "group_chat", "from_bot", "no_sender", "empty", "malformed"],
)
def test_ignored(mutate: str) -> None:
    raw = loadMaxUpdate("text")
    message = raw["message"]
    assert isinstance(message, dict)
    match mutate:
        case "unsupported":
            raw["update_type"] = "message_edited"
        case "group_chat":
            message["recipient"] = {"chat_id": 1, "chat_type": "chat"}
        case "from_bot":
            message["sender"] = {"user_id": 1, "first_name": "b", "is_bot": True}
        case "no_sender":
            message["sender"] = None
        case "empty":
            message["body"] = {"mid": "m1", "seq": 1, "text": "   "}
        case "malformed":
            del message["body"]
    assert mapRawUpdate(raw) is None


# Примечание: одиночный «/» — не команда, а обычный текст
def test_slashOnlyIsText() -> None:
    raw = loadMaxUpdate("text")
    message = raw["message"]
    assert isinstance(message, dict)
    message["body"] = {"mid": "m2", "seq": 1, "text": "/"}
    update = mapRawUpdate(raw)
    assert update is not None
    assert update.kind is UpdateKind.TEXT
