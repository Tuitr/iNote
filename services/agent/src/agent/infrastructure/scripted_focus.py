"""ScriptedBrain: фразы про запись в фокусе — «перенеси его на завтра в 15», «перенеси на час
позже», «отметь выполненной», «удали её». id берётся только из фокуса (ItemBrief), не выдумывается.
"""

import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Final, Literal

type FocusOp = Literal["move", "complete", "delete"]

_PRONOUN: Final = r"(?:\s+(?:её|ее|его|это|эту|этот|её\s+же|запись))?"
_MOVE_RE: Final = re.compile(
    rf"^(?:перенеси|передвинь|сдвинь){_PRONOUN}\s+(?P<rest>.+)$", re.IGNORECASE
)
_SHIFT_RE: Final = re.compile(
    r"^на\s+(?:(?P<hours>\d{1,2})\s+)?час(?:а|ов)?\s+позже$", re.IGNORECASE
)
_COMPLETE_RE: Final = re.compile(
    rf"^(?:отметь|отметить){_PRONOUN}\s+(?:как\s+)?(?:выполнен\w*|сделан\w*)$", re.IGNORECASE
)
_DELETE_RE: Final = re.compile(r"^(?:удали|удалить)\s+(?:её|ее|его|это|эту|этот)$", re.IGNORECASE)
_LEADING_NA_RE: Final = re.compile(r"^на\s+", re.IGNORECASE)


# Примечание: команда над записью в фокусе: что сделать и, для переноса, на когда или на сколько
@dataclass(frozen=True)
class FocusCommand:
    op: FocusOp
    # «перенеси на завтра в 15» — фраза времени без «на» (разбирает ScriptedBrain)
    whenText: str | None = None
    # «перенеси на час позже» — сдвиг от времени записи
    shift: timedelta | None = None


# Примечание: фраза → команда над записью в фокусе; не про фокус — None
def parseFocusCommand(text: str) -> FocusCommand | None:
    if (moved := _MOVE_RE.match(text)) is not None:
        rest = moved["rest"].strip()
        if (shifted := _SHIFT_RE.match(rest)) is not None:
            return FocusCommand("move", shift=timedelta(hours=int(shifted["hours"] or 1)))
        return FocusCommand("move", whenText=_LEADING_NA_RE.sub("", rest))
    if _COMPLETE_RE.match(text):
        return FocusCommand("complete")
    if _DELETE_RE.match(text):
        return FocusCommand("delete")
    return None
