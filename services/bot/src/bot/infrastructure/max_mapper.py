"""Формат MAX живёт только здесь: сырой апдейт → IncomingUpdate (по max-messenger/api-schema)."""

import hashlib
from collections.abc import Mapping
from typing import Final, Literal

from pydantic import ValidationError

from bot.domain.updates import IncomingUpdate, UpdateKind, VoiceRef
from common.logging import getLogger
from common.wire import WireModel

COMMAND_PREFIX: Final = "/"
_UPDATE_ID_DIGEST_BYTES: Final = 16

_log = getLogger(__name__)


# Примечание: пользователь MAX (User)
class MaxUser(WireModel):
    userId: int
    firstName: str = ""
    isBot: bool = False


# Примечание: получатель сообщения (Recipient); для диалога важен chat_id
class MaxRecipient(WireModel):
    chatId: int | None = None
    chatType: Literal["dialog", "chat", "channel"]
    userId: int | None = None


# Примечание: payload медиа-вложения (MediaAttachmentPayload): url + token
class MaxMediaPayload(WireModel):
    url: str
    token: str = ""


# Примечание: вложение; разбираем только audio, остальные типы пропускаем
class MaxAttachment(WireModel):
    type: str
    payload: MaxMediaPayload | None = None


# Примечание: тело сообщения (MessageBody): mid уникален — из него ключ дедупа
class MaxMessageBody(WireModel):
    mid: str
    seq: int = 0
    text: str | None = None
    attachments: list[MaxAttachment] | None = None


# Примечание: сообщение MAX — тип Message в схеме
class MaxMessage(WireModel):
    sender: MaxUser | None = None
    recipient: MaxRecipient
    timestamp: int
    body: MaxMessageBody


# Примечание: нажатие кнопки (Callback)
class MaxCallback(WireModel):
    timestamp: int
    callbackId: str
    payload: str | None = None
    user: MaxUser


# Примечание: апдейт message_created — новое сообщение
class MaxMessageCreated(WireModel):
    updateType: Literal["message_created"]
    timestamp: int
    message: MaxMessage
    userLocale: str | None = None


# Примечание: message_callback; message может быть null, если исходное сообщение удалено
class MaxMessageCallback(WireModel):
    updateType: Literal["message_callback"]
    timestamp: int
    callback: MaxCallback
    message: MaxMessage | None = None
    userLocale: str | None = None


# Примечание: bot_started — пользователь нажал «Начать»; payload — из deep link
class MaxBotStarted(WireModel):
    updateType: Literal["bot_started"]
    timestamp: int
    chatId: int
    user: MaxUser
    payload: str | None = None
    userLocale: str | None = None


SUPPORTED_TYPES: Final = frozenset({"message_created", "message_callback", "bot_started"})


# Примечание: в схеме MAX нет update_id — строим стабильный id из естественного ключа события
def deriveUpdateId(prefix: str, naturalKey: str) -> str:
    digest = hashlib.blake2b(naturalKey.encode(), digest_size=_UPDATE_ID_DIGEST_BYTES)
    return f"{prefix}-{digest.hexdigest()}"


# Примечание: сырой апдейт → IncomingUpdate; неподдержанное и чужое → None с причиной в логе
def mapRawUpdate(raw: Mapping[str, object]) -> IncomingUpdate | None:
    updateType = raw.get("update_type")
    if updateType not in SUPPORTED_TYPES:
        _log.info("update_ignored", reason="unsupported_type", update_type=str(updateType))
        return None
    try:
        match updateType:
            case "message_created":
                return _mapMessage(MaxMessageCreated.fromWire(raw))
            case "message_callback":
                return _mapCallback(MaxMessageCallback.fromWire(raw))
            case _:
                return _mapBotStarted(MaxBotStarted.fromWire(raw))
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        _log.warning("update_malformed", update_type=str(updateType), fields=fields)
        return None


# Примечание: только личные диалоги с людьми: группы, каналы и боты ассистенту не нужны
def _mapMessage(update: MaxMessageCreated) -> IncomingUpdate | None:
    message = update.message
    if message.sender is None or message.sender.isBot:
        _log.info("update_ignored", reason="no_human_sender")
        return None
    if message.recipient.chatType != "dialog":
        _log.info("update_ignored", reason="not_dialog", chat_type=message.recipient.chatType)
        return None
    updateId = deriveUpdateId("msg", message.body.mid)
    userId, chatId = message.sender.userId, message.recipient.chatId
    voice = _findVoice(message.body.attachments or [])
    text = (message.body.text or "").strip()
    name, locale = _name(message.sender), update.userLocale
    if voice is not None:
        return IncomingUpdate(
            updateId, UpdateKind.VOICE, userId, chatId, voice=voice, userName=name, locale=locale
        )
    if text.startswith(COMMAND_PREFIX) and len(text) > len(COMMAND_PREFIX):
        command = text[len(COMMAND_PREFIX) :].split(maxsplit=1)[0]
        return IncomingUpdate(
            updateId,
            UpdateKind.COMMAND,
            userId,
            chatId,
            text=text,
            commandName=command,
            userName=name,
            locale=locale,
        )
    if not text:
        _log.info("update_ignored", reason="empty_message")
        return None
    return IncomingUpdate(
        updateId, UpdateKind.TEXT, userId, chatId, text=text, userName=name, locale=locale
    )


# Примечание: имя для профиля (J8): first_name, пустое — None
def _name(user: MaxUser) -> str | None:
    return user.firstName.strip() or None


# Примечание: голосовое = вложение audio; длительности и mime в схеме MAX нет
def _findVoice(attachments: list[MaxAttachment]) -> VoiceRef | None:
    # TODO(C): подтвердить на живом апдейте, что голосовое приходит как type=audio (долг S4: отчёт
    # claude/s4-zone-c-report.md, «Живой MAX»)
    for attachment in attachments:
        if attachment.type == "audio" and attachment.payload is not None:
            return VoiceRef(url=attachment.payload.url, token=attachment.payload.token)
    return None


# Примечание: callback_id — id клавиатуры, поэтому в ключ дедупа добавляем время нажатия
def _mapCallback(update: MaxMessageCallback) -> IncomingUpdate:
    callback = update.callback
    chatId = update.message.recipient.chatId if update.message is not None else None
    return IncomingUpdate(
        updateId=deriveUpdateId("cb", f"{callback.callbackId}:{callback.timestamp}"),
        kind=UpdateKind.CALLBACK,
        maxUserId=callback.user.userId,
        maxChatId=chatId,
        callbackId=callback.callbackId,
        callbackPayload=callback.payload,
        userName=_name(callback.user),
        locale=update.userLocale,
    )


# Примечание: «Начать»: ключ дедупа — чат и время события
def _mapBotStarted(update: MaxBotStarted) -> IncomingUpdate:
    return IncomingUpdate(
        updateId=deriveUpdateId("start", f"{update.chatId}:{update.timestamp}"),
        kind=UpdateKind.BOT_STARTED,
        maxUserId=update.user.userId,
        maxChatId=update.chatId,
        startPayload=update.payload,
        userName=_name(update.user),
        locale=update.userLocale,
    )
