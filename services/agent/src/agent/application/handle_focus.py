"""POST /v1/focus: запись из доставленного уведомления становится фокусом диалога (S3, шаг 3).

Решение (docs/decisions.md): фокус — записи последнего ответа пользователю; уведомление — тоже
ответ, поэтому фокус = (эта запись,). pending не трогаем: недоделанный вопрос живёт своё (10 мин).
"""

import dataclasses

from agent.application.turn_pipeline import DEFAULT_LOCK_WAIT_S, TurnDeps
from common.errors import AppError, ErrorCode
from common.logging import bindUserId, getLogger
from contracts.focus import FocusRequest

_log = getLogger(__name__)


# Примечание: resolve → лок → контекст с новым фокусом → сохранить; без Brain и без истории
class HandleFocus:
    # Примечание: зависимости — те же, что у хода (resolver, lock, contextStore); лок — столько же
    def __init__(self, deps: TurnDeps, *, lockWaitS: float = DEFAULT_LOCK_WAIT_S) -> None:
        self._deps = deps
        self._lockWaitS = lockWaitS

    # Примечание: лок занят — 503 (бот не повторяет: фокус — подсказка, не данные)
    async def __call__(self, request: FocusRequest) -> None:
        deps = self._deps
        user = await deps.resolver.resolve(request.maxUserId, request.maxChatId, None)
        bindUserId(str(user.userId))
        token = await deps.lock.acquire(user.userId, self._lockWaitS)
        if token is None:
            _log.info("focus_lock_busy")
            raise AppError(ErrorCode.UPSTREAM_UNAVAILABLE, "пользователь занят ходом")
        try:
            context = await deps.contextStore.load(user.userId)
            await deps.contextStore.save(
                user.userId, dataclasses.replace(context, focus=(request.item,))
            )
        finally:
            if not await deps.lock.release(user.userId, token):
                _log.warning("user_lock_lost")
        _log.info("focus_set", kind=request.item.kind)
