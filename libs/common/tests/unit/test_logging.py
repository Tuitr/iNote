import json
import logging
from typing import Final

import pytest

from common.logging import bindUserId, configureLogging, getLogger
from common.trace import traceContext

TRACE: Final = "ab12f0c4d5e6f708192a3b4c5d6e7f80"


# Примечание: последние JSON-строки из stdout
def readJsonLines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


# Примечание: JSON-лог содержит обязательные поля в snake_case
def test_jsonLogHasStandardFields(capsys: pytest.CaptureFixture[str]) -> None:
    configureLogging("bot", "INFO", "json")
    with traceContext(TRACE):
        bindUserId("u-1")
        getLogger("test").info("update_handled", duration_ms=12)
    [entry] = readJsonLines(capsys)
    assert entry["service"] == "bot"
    assert entry["event"] == "update_handled"
    assert entry["trace_id"] == TRACE
    assert entry["user_id"] == "u-1"
    assert entry["duration_ms"] == 12
    assert entry["level"] == "info"
    assert "timestamp" in entry


# Примечание: логи stdlib (uvicorn, библиотеки) идут тем же JSON с service и trace_id
def test_stdlibLogsRoutedThroughStructlog(capsys: pytest.CaptureFixture[str]) -> None:
    configureLogging("agent", "INFO", "json")
    with traceContext(TRACE):
        logging.getLogger("uvicorn.error").warning("порт занят")
    [entry] = readJsonLines(capsys)
    assert entry["service"] == "agent"
    assert entry["event"] == "порт занят"
    assert entry["trace_id"] == TRACE


# Примечание: уровень фильтрует; uvicorn.access приглушён (дубль нашего лога запроса)
def test_levelFiltering(capsys: pytest.CaptureFixture[str]) -> None:
    configureLogging("bot", "WARNING", "json")
    log = getLogger("test")
    log.info("hidden")
    log.warning("shown")
    logging.getLogger("uvicorn.access").info("GET / 200")
    events = [e["event"] for e in readJsonLines(capsys)]
    assert events == ["shown"]


# Примечание: исключение в JSON — строкой в поле exception, одна запись
def test_exceptionRenderedInJson(capsys: pytest.CaptureFixture[str]) -> None:
    configureLogging("bot", "INFO", "json")
    try:
        msg = "boom"
        raise ValueError(msg)
    except ValueError:
        getLogger("test").exception("failed")
    [entry] = readJsonLines(capsys)
    assert "ValueError: boom" in str(entry["exception"])


# Примечание: console-формат читаемый, с событием и полями
def test_consoleFormat(capsys: pytest.CaptureFixture[str]) -> None:
    configureLogging("bot", "INFO", "console")
    getLogger("test").info("started", port=8000)
    out = capsys.readouterr().out
    assert "started" in out
    assert "port=8000" in out
    assert "service=bot" in out
