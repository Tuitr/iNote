"""Общее для действий /v1/actions: вызов инструмента хода, TurnResult из вызова, args кнопки."""

import re
import time
from collections.abc import Mapping
from datetime import datetime
from typing import Final, cast, get_args

from agent.application.actions import ITEM_ID_ARG
from agent.application.item_time import ItemKind
from agent.application.turn_pipeline import Session
from agent.application.turn_rules import briefsFromActions
from common.logging import getLogger
from contracts.agent_side import (
    ExecutedAction,
    ToolErrorCode,
    TurnOutcome,
    TurnResult,
    Usage,
)
from contracts.reply import ArgValue

ACTION_MODEL: Final = "action"
NOT_FOUND: Final = "not_found"
# id записи из кнопки: uuid целиком или первые 8 символов (J3 принимает оба)
_ITEM_ID_RE: Final = re.compile(r"^[0-9a-fA-F-]{8,36}$")
_TOOL_ERRORS: Final = frozenset(get_args(ToolErrorCode.__value__))

_log = getLogger(__name__)


# Примечание: вид записи из фокуса (если она там) — чтобы двигать нужное поле времени
def focusKind(session: Session, itemId: str) -> ItemKind | None:
    brief = next((b for b in session.context.focus if str(b.id).startswith(itemId)), None)
    return brief.kind if brief is not None else None


# Примечание: вызов инструмента хода; результат — как у Brain, для рендера из данных
async def callTool(session: Session, name: str, args: Mapping[str, object]) -> ExecutedAction:
    result = await session.tools.call(name, args)
    _log.info("action_tool_called", tool=name, ok=result.ok, error_code=_code(result.error))
    return ExecutedAction(name=name, args=dict(args), result=result)


# Примечание: TurnResult из одного вызова: ошибка инструмента → outcome=error с его кодом
def toolResult(action: ExecutedAction, success: TurnOutcome, started: float) -> TurnResult:
    usage = Usage(
        model=ACTION_MODEL,
        llmCalls=0,
        tokensIn=0,
        tokensOut=0,
        latencyMs=round((time.perf_counter() - started) * 1000),
    )
    code = errorCode(action)
    if code is not None:
        known = cast("ToolErrorCode", code if code in _TOOL_ERRORS else "internal")
        return TurnResult(outcome="error", actions=[action], errorCode=known, usage=usage)
    focus = list(briefsFromActions([action]))
    return TurnResult(outcome=success, actions=[action], focus=focus, usage=usage)


# Примечание: код ошибки вызова; None — успех
def errorCode(action: ExecutedAction) -> str | None:
    return None if action.result.ok else (_code(action.result.error) or "internal")


# Примечание: код из ErrorInfo без падения на None
def _code(error: object) -> str | None:
    code = getattr(error, "code", None)
    return code if isinstance(code, str) else None


# Примечание: id записи из args кнопки; не строка или не похоже на uuid — None
def itemIdArg(args: Mapping[str, ArgValue]) -> str | None:
    raw = args.get(ITEM_ID_ARG)
    return raw.lower() if isinstance(raw, str) and _ITEM_ID_RE.match(raw) else None


# Примечание: время из args — ISO 8601 строго со смещением (правило 2)
def momentArg(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.utcoffset() is not None else None
