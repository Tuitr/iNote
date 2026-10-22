"""DialogLog по J8: POST /internal/v1/dialog-turns в mcp-контейнере — фоном в TaskRegistry.

Ответ пользователю запись не ждёт. Вызов идемпотентен по trace_id (ключ dialog-turns:<trace_id>),
поэтому сеть, 5xx и таймаут повторяются (2 повтора у клиента). Сбой — warning с trace_id,
без текста.
При остановке реестр дожидается начатых записей; не успевшие — в лог как потерянные.
"""

from typing import Final

from agent.application.ports import DialogEntry
from common.errors import AppError
from common.http import ServiceHttpClient
from common.lifecycle import ShuttingDownError, TaskRegistry
from common.logging import getLogger
from contracts.dialog_turns import (
    DIALOG_TURNS_PATH,
    TEXT_MAX_LENGTH,
    AssistantDialogTurn,
    DialogTurnsRequest,
    UserDialogTurn,
)

TASK_PREFIX: Final = "dialog-turns"

_log = getLogger(__name__)


# Примечание: запись хода в историю D; http — клиент к корню mcp-контейнера с повторами
class HttpDialogLogWriter:
    # Примечание: tasks — реестр приложения: запись переживает ответ, но не остановку сервиса
    def __init__(self, http: ServiceHttpClient, tasks: TaskRegistry) -> None:
        self._http = http
        self._tasks = tasks

    # Примечание: ставит запись в фон и сразу возвращается; сервис останавливается — запись теряется
    def record(self, entry: DialogEntry) -> None:
        try:
            # задачу держит реестр (ждёт её при остановке), ссылка здесь не нужна
            _ = self._tasks.spawn(self._send(entry), name=f"{TASK_PREFIX}:{entry.traceId}")
        except ShuttingDownError:
            _log.warning("dialog_turns_lost", trace_id=entry.traceId, reason="shutting_down")

    # Примечание: POST с ключом от trace_id; любая ошибка — warning, наружу не уходит. Задача
    # наследует contextvars хода: X-Trace-Id и user_id в логах — той же цепочки
    async def _send(self, entry: DialogEntry) -> None:
        request = _request(entry)
        try:
            await self._http.post(
                DIALOG_TURNS_PATH,
                json=request.toWire(),
                idempotencyKey=f"{TASK_PREFIX}:{entry.traceId}",
            )
        except AppError as exc:
            _log.warning("dialog_turns_failed", trace_id=entry.traceId, error_code=exc.code)
            return
        _log.info("dialog_turns_saved", trace_id=entry.traceId, latency_ms=entry.latencyMs)


# Примечание: DialogEntry → тело J8; тексты обрезаются до лимита контракта
def _request(entry: DialogEntry) -> DialogTurnsRequest:
    return DialogTurnsRequest(
        userId=entry.userId,
        traceId=entry.traceId,
        turns=(
            UserDialogTurn(text=entry.said.text[:TEXT_MAX_LENGTH], inputType=entry.said.inputType),
            AssistantDialogTurn(text=entry.answer[:TEXT_MAX_LENGTH], itemIds=list(entry.itemIds)),
        ),
        latencyMs=entry.latencyMs,
    )
