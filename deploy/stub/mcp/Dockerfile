# ВРЕМЕННАЯ MCP-заглушка зоны C (S1). После мержа — заглушка D, затем build: services/mcp.
# Версии закреплены как в uv.lock агента: заглушка и клиент говорят на одной версии протокола.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install "mcp==2.3.0" "uvicorn==0.54.0"
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /stub
# --chmod: в рабочей копии файлы бывают 600, а COPY сохраняет права — пользователь app не прочтёт
COPY --chmod=0444 server.py ./server.py
COPY --chmod=0555 fixtures/ ./fixtures/
USER app
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["python", "/stub/server.py"]
