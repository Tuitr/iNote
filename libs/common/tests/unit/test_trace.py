import asyncio

import structlog

from common.trace import (
    currentTraceId,
    isValidTraceId,
    newTraceId,
    shortTraceId,
    traceContext,
)


# Примечание: формат trace_id — 32 hex, каждый раз новый
def test_newTraceIdIs32HexAndUnique() -> None:
    ids = {newTraceId() for _ in range(100)}
    assert len(ids) == 100
    assert all(isValidTraceId(i) for i in ids)


# Примечание: принимаем только каноничный вид: длина, регистр, алфавит
def test_isValidTraceIdRejectsNonCanonical() -> None:
    assert isValidTraceId("ab12f0c4d5e6f708192a3b4c5d6e7f80")
    assert not isValidTraceId("AB12F0C4D5E6F708192A3B4C5D6E7F80")
    assert not isValidTraceId("ab12f0")
    assert not isValidTraceId("zz12f0c4d5e6f708192a3b4c5d6e7f80")
    assert not isValidTraceId("")


# Примечание: пользователю показываем первые 6 символов
def test_shortTraceId() -> None:
    assert shortTraceId("ab12f0c4d5e6f708192a3b4c5d6e7f80") == "ab12f0"
    assert shortTraceId() == ""


# Примечание: контекст ставит и снимает trace_id, вложенный — восстанавливает внешний
def test_traceContextSetsAndRestores() -> None:
    assert currentTraceId() is None
    with traceContext("a" * 32):
        assert currentTraceId() == "a" * 32
        assert structlog.contextvars.get_contextvars()["trace_id"] == "a" * 32
        with traceContext("b" * 32):
            assert currentTraceId() == "b" * 32
        assert currentTraceId() == "a" * 32
    assert currentTraceId() is None
    assert "trace_id" not in structlog.contextvars.get_contextvars()


# Примечание: параллельные задачи не видят trace_id друг друга
async def test_traceIsolatedBetweenTasks() -> None:
    # Примечание: держит свой trace_id через await
    async def worker(traceId: str) -> str | None:
        with traceContext(traceId):
            await asyncio.sleep(0.01)
            return currentTraceId()

    results = await asyncio.gather(worker("1" * 32), worker("2" * 32))
    assert list(results) == ["1" * 32, "2" * 32]
