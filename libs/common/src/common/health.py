import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from common.logging import getLogger

if TYPE_CHECKING:
    from redis.asyncio import Redis

type CheckStatus = Literal["ok", "fail", "timeout"]
type Probe = Callable[[], Awaitable[object]]

DEFAULT_CHECK_TIMEOUT_S: Final = 1.0

_log = getLogger(__name__)


# Примечание: одна проверка readiness: probe падает исключением, если зависимость недоступна
@dataclass(frozen=True)
class HealthCheck:
    probe: Probe
    timeoutS: float = DEFAULT_CHECK_TIMEOUT_S


# Примечание: выполняет проверку со своим таймаутом; причину пишет в лог, наружу — только статус
async def _runCheck(name: str, check: HealthCheck) -> CheckStatus:
    try:
        async with asyncio.timeout(check.timeoutS):
            await check.probe()
    except TimeoutError:
        _log.warning("health_check_timeout", check=name, timeout_s=check.timeoutS)
        return "timeout"
    except Exception as exc:  # noqa: BLE001 — любая ошибка зависимости = «не готов»
        _log.warning("health_check_failed", check=name, error=type(exc).__name__)
        return "fail"
    return "ok"


# Примечание: /health/live — процесс жив; /health/ready — зависимости, 503 при любой неудаче
def buildHealthRouter(checks: Mapping[str, HealthCheck]) -> APIRouter:
    router = APIRouter(prefix="/health", tags=["health"])
    frozenChecks = dict(checks)

    # Примечание: liveness не трогает зависимости — иначе оркестратор перезапустит здоровый процесс
    @router.get("/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    # Примечание: проверки идут параллельно, общее время ≤ самого долгого таймаута
    @router.get("/ready")
    async def ready() -> JSONResponse:
        names = list(frozenChecks)
        results = await asyncio.gather(*(_runCheck(n, frozenChecks[n]) for n in names))
        statuses = dict(zip(names, results, strict=True))
        healthy = all(s == "ok" for s in results)
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "degraded", "checks": statuses},
        )

    return router


# Примечание: проверка Redis — PING
def redisHealthCheck(client: "Redis", timeoutS: float = DEFAULT_CHECK_TIMEOUT_S) -> HealthCheck:
    # Примечание: результат PING не важен, важна ошибка
    async def probe() -> object:
        return await client.ping()

    return HealthCheck(probe=probe, timeoutS=timeoutS)
