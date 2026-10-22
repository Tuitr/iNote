"""Каталог действий /v1/actions (кнопки и команды без LLM); утверждается на G3.

См. docs/decisions.md.
"""

from typing import Final

PICK_OPTION: Final = "pick_option"
CANCEL: Final = "cancel"
UNDO: Final = "undo"
RESCHEDULE: Final = "reschedule"
LIST_PERIOD: Final = "list_period"
START: Final = "start"
HELP: Final = "help"

# аргументы кнопок
CHOICE_ARG: Final = "choice"
ITEM_ID_ARG: Final = "item_id"
STARTS_AT_ARG: Final = "starts_at"
PERIOD_ARG: Final = "period"
