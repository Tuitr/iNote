import httpx
import structlog

from common.errors import AppError, ErrorCode
from common.trace import isValidTraceId


# Примечание: AppError → конверт с кодом, сообщением, деталями и trace_id
async def test_appErrorEnvelope(appClient: httpx.AsyncClient) -> None:
    response = await appClient.get("/missing")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["message"] == "запись не найдена"
    assert error["details"] == {"item_id": "7f3a"}
    assert error["trace_id"] == response.headers["X-Trace-Id"]


# Примечание: непойманное исключение → 500 internal без стектрейса и текста исключения
async def test_unhandledErrorIs500WithoutStack(appClient: httpx.AsyncClient) -> None:
    with structlog.testing.capture_logs() as logs:
        response = await appClient.get("/boom")
    assert response.status_code == 500
    body = response.json()
    assert body["error"]["code"] == "internal"
    assert "секретная" not in response.text
    assert "Traceback" not in response.text
    assert isValidTraceId(body["error"]["trace_id"])
    assert any(entry["event"] == "unhandled_error" for entry in logs)


# Примечание: ошибка валидации → validation_error 400 без входных значений
async def test_validationErrorEnvelope(appClient: httpx.AsyncClient) -> None:
    response = await appClient.get("/items/not-a-number")
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]["fields"][0]["loc"] == ["path", "itemId"]
    assert "not-a-number" not in response.text


# Примечание: неизвестный роут → not_found в том же конверте
async def test_unknownRouteIsNotFoundEnvelope(appClient: httpx.AsyncClient) -> None:
    response = await appClient.get("/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


# Примечание: статус берётся из таблицы стандарта; чужой код — с явным статусом или 500
def test_appErrorStatusTable() -> None:
    assert AppError(ErrorCode.RATE_LIMITED, "x").httpStatus == 429
    assert AppError(ErrorCode.UPSTREAM_TIMEOUT, "x").httpStatus == 504
    assert AppError("no_speech", "x", httpStatus=422).httpStatus == 422
    assert AppError("unknown_code", "x").httpStatus == 500
