"""Фикстуры e2e и отчёт прогона: клиент к dev-роутам бота на поднятом стенде (make up / make e2e).

В конце прогона — таблица «сценарий → ok/fail → trace_id» (последний ответ бота в сценарии;
путь запроса — `make logs TRACE=<первые 6>`). Сценарий — первая строка примечания над тестом.
"""

import inspect
from collections.abc import AsyncIterator, Generator
from dataclasses import dataclass
from typing import Final

import httpx
import pytest
import pytest_asyncio
from _pytest.terminal import TerminalReporter
from tests.e2e.stand import BOT_URL, SEEN_TRACES

NOTE_PREFIX: Final = "# Примечание: "


# Примечание: строка отчёта: сценарий, итог, trace_id последнего ответа бота
@dataclass(frozen=True)
class ScenarioResult:
    file: str
    scenario: str
    outcome: str
    traceId: str


RESULTS: Final[list[ScenarioResult]] = []


# Примечание: клиент к dev-роутам бота; стенд не поднят — понятная ошибка, а не таймаут
@pytest_asyncio.fixture
async def bot() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=BOT_URL, timeout=5) as client:
        try:
            ready = await client.get("/health/ready")
        except httpx.TransportError as exc:
            pytest.fail(f"стенд не поднят ({BOT_URL}): make up или make e2e — {exc}")
        assert ready.status_code == 200, ready.text
        yield client


# Примечание: у каждого теста свой список увиденных trace_id
@pytest.fixture(autouse=True)
def freshTraces() -> None:
    SEEN_TRACES.clear()


# Примечание: название сценария — первая строка примечания над тестом и параметр (text/voice)
def scenarioName(item: pytest.Item) -> str:
    function = getattr(item, "function", None)
    comment = inspect.getcomments(function) if function is not None else None
    first = (comment or "").splitlines()[0] if comment else item.name
    name = first.removeprefix(NOTE_PREFIX).strip()
    callspec = getattr(item, "callspec", None)
    return f"{name} [{callspec.id}]" if callspec is not None else name


# Примечание: итог фазы call каждого теста — в отчёт (ошибка setup — тоже fail)
@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item) -> Generator[None, pytest.TestReport]:
    report = yield
    result = report.get_result() if hasattr(report, "get_result") else report
    if result.when == "call" or (result.when == "setup" and result.failed):
        outcome = "ok" if result.passed else "fail"
        trace = SEEN_TRACES[-1] if SEEN_TRACES else "—"
        RESULTS.append(ScenarioResult(item.path.name, scenarioName(item), outcome, trace))


# Примечание: таблица в конце прогона: файл, сценарий, ok/fail, trace_id
def pytest_terminal_summary(terminalreporter: TerminalReporter) -> None:
    if not RESULTS:
        return
    terminalreporter.section("отчёт e2e: сценарий → итог → trace_id")
    for result in RESULTS:
        line = f"{result.outcome:<4} {result.traceId:<32}  {result.file}: {result.scenario}"
        terminalreporter.write_line(line[:200])
    failed = sum(1 for r in RESULTS if r.outcome != "ok")
    terminalreporter.write_line(f"сценариев: {len(RESULTS)}, не прошло: {failed}")
