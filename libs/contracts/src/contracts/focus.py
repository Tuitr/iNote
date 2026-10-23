"""bot (C) → agent (C): POST /v1/focus — «эта запись сейчас в фокусе». Владелец C, внутренний.

Бот зовёт после доставленного напоминания (J4): следующая фраза «перенеси его на завтра в 15»
относится к этой записи. Ответ 204; сбой бот только пишет в warning — уведомление уже доставлено.
"""

from typing import Final

from common.wire import WireModel
from contracts.agent_side import ItemBrief

FOCUS_PATH: Final = "/v1/focus"


# Примечание: кто получил уведомление и про какую запись (поля ItemBrief — из NotifyItem)
class FocusRequest(WireModel):
    maxUserId: int
    maxChatId: int
    item: ItemBrief
