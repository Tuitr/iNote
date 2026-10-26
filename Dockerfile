# Один образ на весь блок роли D: mcp, api, scheduler и migrate
# отличаются только командой запуска, которую задаёт compose.
# Зависимости ставятся строго по uv.lock, как в образах C (deploy/docker/python.Dockerfile).
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.24

FROM ${UV_IMAGE} AS uv

FROM python:3.12-slim

COPY --from=uv /uv /bin/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Сначала только манифесты: слой зависимостей пересобирается, лишь когда меняется uv.lock.
# Манифесты членов workspace нужны uv, чтобы проверить lock; их код в образ D не попадает.
COPY pyproject.toml uv.lock ./
COPY libs/common/pyproject.toml libs/common/
COPY libs/contracts/pyproject.toml libs/contracts/
COPY services/bot/pyproject.toml services/bot/
COPY services/agent/pyproject.toml services/agent/
# Только зависимости корневого проекта D, без dev-группы; сам код D ставить не нужно —
# он лежит в /app и импортируется от корня (libs.core, services.mcp).
RUN uv sync --frozen --no-dev --no-install-project

# Код блока D и общие контракты: папки других ролей в libs/ и services/ сюда не попадают.
COPY libs/__init__.py ./libs/
COPY libs/contracts ./libs/contracts
COPY libs/core ./libs/core
COPY services/__init__.py services/http_common.py services/http_errors.py services/seed.py ./services/
COPY services/api ./services/api
COPY services/mcp ./services/mcp
COPY services/scheduler ./services/scheduler
# Миграции: их накатывает контейнер migrate (alembic upgrade head).
COPY alembic.ini ./
COPY migrations ./migrations
# Примеры J5: из них api строит примеры ответов в OpenAPI.
COPY contracts/examples ./contracts/examples

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
CMD ["uvicorn", "services.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
