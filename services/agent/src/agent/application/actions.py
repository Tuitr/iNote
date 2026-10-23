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
# S3: кнопки напоминаний и конфликта (предложение к каталогу G3)
COMPLETE: Final = "complete"
SNOOZE: Final = "snooze"
CONFIRM_CONFLICT: Final = "confirm_conflict"
# S3: /settings — часовой пояс (предложение к каталогу G3)
SETTINGS: Final = "settings"
SET_TZ: Final = "set_tz"

# аргументы кнопок
CHOICE_ARG: Final = "choice"
ITEM_ID_ARG: Final = "item_id"
STARTS_AT_ARG: Final = "starts_at"
PERIOD_ARG: Final = "period"
# вид записи у «Изменить время»: какое поле времени двигать (application/item_time.py)
KIND_ARG: Final = "kind"
# snooze: на сколько минут или «tomorrow» — то же время завтра
MINUTES_ARG: Final = "minutes"
UNTIL_ARG: Final = "until"
UNTIL_TOMORROW: Final = "tomorrow"
# confirm_conflict: что создать вопреки пересечению — из того вызова, что вернул conflict
TITLE_ARG: Final = "title"
# set_tz: пояс IANA из белого списка (domain/timezones.py)
TZ_ARG: Final = "tz"
ENDS_AT_ARG: Final = "ends_at"
