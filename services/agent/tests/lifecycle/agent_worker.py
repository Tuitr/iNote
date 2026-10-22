"""Процесс для lifecycle-теста: агент с медленным Brain под uvicorn, SIGTERM посреди хода."""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import uvicorn

from agent.infrastructure.settings import AgentSettings
from agent.main import AgentOverrides, createApp
from contracts.agent_side import ToolExecutor, TurnInput, TurnResult, Usage


# Примечание: Brain, который думает delayS секунд — SIGTERM приходит посреди хода
class SlowBrain:
    # Примечание: длительность хода из аргументов процесса
    def __init__(self, delayS: float) -> None:
        self.delayS = delayS

    # Примечание: ждёт и отвечает текстом
    async def run(self, turn: TurnInput, tools: ToolExecutor) -> TurnResult:
        await asyncio.sleep(self.delayS)
        usage = Usage(model="slow", llmCalls=1, tokensIn=1, tokensOut=1, latencyMs=1)
        return TurnResult(outcome="answer", say="Дождался ответа", usage=usage)

    # Примечание: уточнения — S2
    async def resume(self, turn: TurnInput, choice: str, tools: ToolExecutor) -> TurnResult:
        raise NotImplementedError


# Примечание: строка в файл (из потока — не блокирует цикл событий)
def appendLine(path: Path, line: bytes) -> None:
    with path.open("ab") as out:
        out.write(line + b"\n")


# Примечание: служебный REST mcp-контейнера без сети: user_id = max_user_id, health → 200;
# dialog-turns «думает» DIALOG_DELAY_S и дописывает тело в DIALOG_FILE — тест видит запись
async def fakeMcpHttp(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/internal/v1/users/resolve":
        userId = json.loads(request.content)["max_user_id"]
        return httpx.Response(200, json={"user_id": userId, "tz": "Europe/Moscow"})
    if request.url.path == "/internal/v1/dialog-turns":
        await asyncio.sleep(float(os.environ.get("DIALOG_DELAY_S", "0")))
        await asyncio.to_thread(appendLine, Path(os.environ["DIALOG_FILE"]), request.content)
        return httpx.Response(202, json={"accepted": True})
    return httpx.Response(200, json={"status": "ok"})


# Примечание: аргументы — порт и длительность хода; окружение (Redis, токен) задаёт тест
def main(port: int, delayS: float) -> None:
    app = createApp(
        AgentSettings(),
        AgentOverrides(brain=SlowBrain(delayS), mcpTransport=httpx.MockTransport(fakeMcpHttp)),
    )
    # log_config=None: логи уже настроены createApp (JSON); иначе uvicorn перенастроит их на текст
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, timeout_graceful_shutdown=15, log_config=None
    )
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main(int(sys.argv[1]), float(sys.argv[2]))
