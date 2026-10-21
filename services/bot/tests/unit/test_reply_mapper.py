"""Reply агента → сообщение MAX: кнопки, ключи callback, open_app/ссылка, лимиты рядов."""

import pytest
from syrupy.assertion import SnapshotAssertion
from syrupy.extensions.json import JSONSnapshotExtension

from bot.application.reply_mapper import OpenAppMode, ReplyToMaxMapper
from bot.domain.messages import ButtonKind, KeyboardButton, MessageFormat, OutgoingMessage
from bot.infrastructure.max_outgoing import toNewMessageBody, typingBody
from contracts.reply import ActionButton, OpenAppButton, Reply, ReplyState
from services.bot.tests.conftest import BOT_USERNAME
from services.bot.tests.fakes import InMemoryCallbackStore, exampleReply

TRACE = "ab12f0c4d5e6f708192a3b4c5d6e7f80"


# Примечание: снапшоты тела POST /messages — видно, что именно получит MAX
@pytest.fixture
def jsonSnapshot(snapshot: SnapshotAssertion) -> SnapshotAssertion:
    return snapshot.use_extension(JSONSnapshotExtension)


# Примечание: маппер в нужном режиме и его хранилище кнопок
def mapper(
    mode: OpenAppMode = OpenAppMode.OPEN_APP, username: str = BOT_USERNAME
) -> tuple[ReplyToMaxMapper, InMemoryCallbackStore]:
    store = InMemoryCallbackStore()
    return ReplyToMaxMapper(store, mode=mode, botUsername=username), store


# Примечание: примеры Reply из contracts/examples в обоих режимах кнопки приложения
@pytest.mark.parametrize("name", ["confirm.json", "list.json", "error.json"])
@pytest.mark.parametrize("mode", list(OpenAppMode))
async def test_examples(name: str, mode: OpenAppMode, jsonSnapshot: SnapshotAssertion) -> None:
    mapped, _ = mapper(mode)
    message = await mapped.toMessage(exampleReply(name), chatId=5)
    assert toNewMessageBody(message) == jsonSnapshot


# Примечание: action → callback с коротким ключом; за ключом — ровно action и args из Reply
async def test_actionStoredBehindKey() -> None:
    mapped, store = mapper()
    message = await mapped.toMessage(exampleReply("confirm.json"), chatId=5)
    undo, reschedule = message.keyboard[0]
    assert (undo.kind, undo.text) == (ButtonKind.CALLBACK, "Отменить")
    assert undo.payload is not None
    stored = store.saved[undo.payload]
    assert (stored.action, stored.args) == (
        "undo",
        {"item_id": "7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90"},
    )
    assert reschedule.payload != undo.payload


# Примечание: open_app — web_app = имя бота, payload = start_param; link — deep link со startapp
async def test_appButtonModes() -> None:
    reply = exampleReply("confirm.json")
    openApp, _ = mapper(OpenAppMode.OPEN_APP)
    [button] = (await openApp.toMessage(reply, chatId=5)).keyboard[1]
    assert (button.kind, button.webApp, button.payload) == (
        ButtonKind.OPEN_APP,
        BOT_USERNAME,
        "item_7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90",
    )
    link, _ = mapper(OpenAppMode.LINK, username="@va_test_bot")
    [button] = (await link.toMessage(reply, chatId=5)).keyboard[1]
    assert button.kind is ButtonKind.LINK
    assert (
        button.url
        == f"https://max.ru/{BOT_USERNAME}?startapp=item_7f3a9c2e-5b1d-4c8e-9a21-0d4e5f6a8b90"
    )


# Примечание: без имени бота open_app уходит без web_app, а ссылку не построить — кнопка пропадает
async def test_withoutUsername() -> None:
    reply = exampleReply("confirm.json")
    openApp, _ = mapper(OpenAppMode.OPEN_APP, username="")
    [button] = (await openApp.toMessage(reply, chatId=5)).keyboard[1]
    assert button.webApp is None
    link, _ = mapper(OpenAppMode.LINK, username="")
    assert len((await link.toMessage(reply, chatId=5)).keyboard) == 1


# Примечание: ряд из 7 кнопок с 4 приложениями делится: в ряду MAX до 3 ссылок или open_app
async def test_rowSplitByAppLimit() -> None:
    apps = [OpenAppButton(text=f"День {i}", startParam=f"day_2026-10-2{i}") for i in range(1, 5)]
    actions = [ActionButton(text=f"Д{i}", action="noop") for i in range(3)]
    reply = Reply(text="x", state=ReplyState.ANSWER, buttons=[[*apps[:3], *actions]], traceId=TRACE)
    mapped, _ = mapper()
    message = await mapped.toMessage(
        reply.model_copy(update={"buttons": [[*apps[:3], *actions]]}), 5
    )
    assert [len(row) for row in message.keyboard] == [6]
    many = Reply.model_construct(
        text="x",
        format=reply.format,
        state=ReplyState.ANSWER,
        buttons=[[*apps, *actions]],
        items=[],
        speak=False,
        traceId=TRACE,
    )
    message = await mapped.toMessage(many, chatId=5)
    assert [len(row) for row in message.keyboard] == [3, 4]
    assert [sum(b.isAppLike for b in row) for row in message.keyboard] == [3, 1]


# Примечание: формат — markdown только когда его выставил агент; иначе plain без поля format
async def test_format() -> None:
    mapped, _ = mapper()
    error = await mapped.toMessage(exampleReply("error.json"), chatId=5)
    assert error.format is MessageFormat.PLAIN
    assert "format" not in toNewMessageBody(error)
    confirm = await mapped.toMessage(exampleReply("confirm.json"), chatId=5)
    assert toNewMessageBody(confirm)["format"] == "markdown"


# Примечание: клавиатура проверяется в домене — кривая не дойдёт до MAX
@pytest.mark.parametrize(
    "keyboard",
    [
        ((),),
        (tuple(KeyboardButton(ButtonKind.CALLBACK, "x", payload="p") for _ in range(8)),),
        (tuple(KeyboardButton(ButtonKind.LINK, "x", url="https://max.ru") for _ in range(4)),),
    ],
    ids=["empty_row", "eight_buttons", "four_links"],
)
def test_keyboardValidated(keyboard: tuple[tuple[KeyboardButton, ...], ...]) -> None:
    with pytest.raises(ValueError, match="ряд"):
        OutgoingMessage(chatId=1, text="x", keyboard=keyboard)


# Примечание: кнопка без обязательного поля — ошибка сразу
def test_buttonValidated() -> None:
    with pytest.raises(ValueError, match="url"):
        KeyboardButton(ButtonKind.LINK, "x")
    with pytest.raises(ValueError, match="payload"):
        KeyboardButton(ButtonKind.CALLBACK, "x")


# Примечание: «печатает…» — typing_on
def test_typingBody() -> None:
    assert typingBody() == {"action": "typing_on"}
