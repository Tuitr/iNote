# Один образ на весь блок роли D: mcp, api (и позже scheduler, migrate)
# отличаются только командой запуска, которую задаёт compose.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Копируем только код блока D и общие контракты: папки других ролей
# в libs/ и services/ в этот образ попадать не должны.
COPY pyproject.toml ./
COPY libs/__init__.py ./libs/
COPY libs/contracts ./libs/contracts
COPY libs/core ./libs/core
COPY services/__init__.py services/http_common.py services/http_errors.py ./services/
COPY services/api ./services/api
COPY services/mcp ./services/mcp
# Миграции: их накатывает контейнер migrate (alembic upgrade head).
COPY alembic.ini ./
COPY migrations ./migrations
# Примеры J3: на них отвечает MCP-заглушка.
COPY contracts/examples ./contracts/examples
# Установка в режиме -e оставляет код в /app, поэтому пути к contracts/examples
# считаются от исходников одинаково локально и в образе.
RUN pip install --no-cache-dir -e .

RUN useradd --create-home --uid 10001 app
USER app

EXPOSE 8000
CMD ["uvicorn", "services.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
