# Общий образ Python-сервисов (bot, agent, asr; позже — mcp, api, scheduler у владельцев).
#   docker build -f deploy/docker/python.Dockerfile --build-arg PACKAGE=va-bot --build-arg MODULE=bot .
# Контекст сборки — корень репо: нужен весь uv workspace и uv.lock.

ARG PYTHON_IMAGE=python:3.12-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.24

FROM ${UV_IMAGE} AS uv

# --- сборка: зависимости строго по uv.lock, без dev-группы, пакеты не editable ---
FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /src
ARG PACKAGE
RUN test -n "${PACKAGE}" || (echo "нужен --build-arg PACKAGE=va-<сервис>" >&2 && exit 1)
# Сначала только манифесты — слой зависимостей кешируется, пока не меняется uv.lock
COPY pyproject.toml uv.lock ./
COPY libs/common/pyproject.toml libs/common/
COPY libs/contracts/pyproject.toml libs/contracts/
COPY libs/brain/pyproject.toml libs/brain/
COPY services/bot/pyproject.toml services/bot/
COPY services/agent/pyproject.toml services/agent/
COPY services/asr/pyproject.toml services/asr/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-workspace --package "${PACKAGE}"
COPY libs libs
COPY services services
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --package "${PACKAGE}"

# --- рантайм: только venv и примеры контрактов, без uv и исходников ---
FROM ${PYTHON_IMAGE} AS runtime
ARG MODULE
RUN test -n "${MODULE}" || (echo "нужен --build-arg MODULE=<пакет python>" >&2 && exit 1) \
 && groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
# Агент в S0 отдаёт ответы из contracts/examples. chmod: в рабочей копии файлы бывают 600,
# а COPY сохраняет права — без него пользователь app не прочитает примеры
COPY contracts contracts
RUN chmod -R a+rX /app/contracts
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_FACTORY=${MODULE}.main:createApp
USER app
EXPOSE 8000
STOPSIGNAL SIGTERM
# exec — uvicorn становится PID 1 и сам получает SIGTERM (штатная остановка, lifecycle)
CMD ["sh", "-c", "exec uvicorn \"$APP_FACTORY\" --factory --host 0.0.0.0 --port 8000 --no-server-header --timeout-graceful-shutdown 15"]
