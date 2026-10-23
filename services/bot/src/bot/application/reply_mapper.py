"""Reply агента → сообщение MAX: action → callback-кнопка с ключом, open_app → open_app или ссылка.

Бот не знает бизнес-логики (правило 7): он только раскладывает абстрактный Reply по правилам MAX —
до 7 кнопок и до 3 ссылок или open_app в ряду, до 30 рядов, текст до 4000 символов.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Final
from urllib.parse import quote

from bot.application.ports import CallbackStore, StoredAction
from bot.domain.messages import (
    MAX_APP_BUTTONS_PER_ROW,
    MAX_BUTTONS_PER_ROW,
    MAX_KEYBOARD_ROWS,
    ButtonKind,
    KeyboardButton,
    MessageFormat,
    OutgoingMessage,
    fitToLimit,
)
from bot.domain.updates import MaxChatId
from common.logging import getLogger
from contracts.reply import ActionButton, OpenAppButton, Reply, ReplyButton, ReplyFormat

# Запасной путь в мини-приложение: deep link бота со start_param (J6)
APP_LINK_TEMPLATE: Final = "https://max.ru/{bot}?startapp={param}"

_log = getLogger(__name__)


# Примечание: как открывать мини-приложение: кнопкой open_app (payload = start_param) или ссылкой
class OpenAppMode(StrEnum):
    OPEN_APP = "open_app"
    LINK = "link"


# Примечание: перевод Reply в OutgoingMessage; callback-ключи пишет в CallbackStore
class ReplyToMaxMapper:
    # Примечание: botUsername — web_app у open_app и адрес deep link; пусто — open_app без web_app
    def __init__(self, callbacks: CallbackStore, *, mode: OpenAppMode, botUsername: str) -> None:
        self._callbacks = callbacks
        self._mode = mode
        self._botUsername = botUsername.lstrip("@")

    # Примечание: Reply → сообщение в чат; формат — markdown только если агент его выставил
    async def toMessage(self, reply: Reply, chatId: MaxChatId) -> OutgoingMessage:
        rows: list[list[KeyboardButton]] = []
        for row in reply.buttons:
            buttons = [
                b for b in [await self._button(button, chatId) for button in row] if b is not None
            ]
            rows.extend(_splitRow(buttons))
        if len(rows) > MAX_KEYBOARD_ROWS:
            _log.warning("keyboard_rows_trimmed", rows=len(rows), limit=MAX_KEYBOARD_ROWS)
            rows = rows[:MAX_KEYBOARD_ROWS]
        markdown = reply.format is ReplyFormat.MARKDOWN
        return OutgoingMessage(
            chatId=chatId,
            text=fitToLimit(reply.text),
            format=MessageFormat.MARKDOWN if markdown else MessageFormat.PLAIN,
            keyboard=tuple(tuple(row) for row in rows),
        )

    # Примечание: одна кнопка Reply → кнопка MAX; без адреса бота ссылку не построить — пропуск;
    # действие кнопки привязано к чату, куда она ушла
    async def _button(self, button: ReplyButton, chatId: MaxChatId) -> KeyboardButton | None:
        if isinstance(button, ActionButton):
            stored = StoredAction(
                button.action, dict(button.args), chatId=chatId, label=button.text
            )
            key = await self._callbacks.save(stored)
            return KeyboardButton(ButtonKind.CALLBACK, button.text, payload=key)
        return self._appButton(button)

    # Примечание: open_app с payload = start_param или ссылка max.ru/<бот>?startapp=…
    def _appButton(self, button: OpenAppButton) -> KeyboardButton | None:
        param = str(button.startParam)
        if self._mode is OpenAppMode.OPEN_APP:
            return KeyboardButton(
                ButtonKind.OPEN_APP, button.text, payload=param, webApp=self._botUsername or None
            )
        if not self._botUsername:
            _log.warning("app_link_skipped", reason="no_bot_username")
            return None
        url = APP_LINK_TEMPLATE.format(bot=quote(self._botUsername), param=quote(param))
        return KeyboardButton(ButtonKind.LINK, button.text, url=url)


# Примечание: ряд длиннее лимитов MAX делим на несколько, порядок кнопок сохраняется
def _splitRow(buttons: Sequence[KeyboardButton]) -> list[list[KeyboardButton]]:
    rows: list[list[KeyboardButton]] = []
    current: list[KeyboardButton] = []
    for button in buttons:
        apps = sum(b.isAppLike for b in current) + button.isAppLike
        if current and (len(current) == MAX_BUTTONS_PER_ROW or apps > MAX_APP_BUTTONS_PER_ROW):
            rows.append(current)
            current = []
        current.append(button)
    if current:
        rows.append(current)
    return rows
