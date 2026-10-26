# ВРЕМЕННАЯ ASR-заглушка зоны C (S2). После мержа — ASR_MODE=fake от A, затем build: services/asr.
# Версии — как в uv.lock workspace.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install "fastapi==0.156.8" "uvicorn==0.54.0" "python-multipart==0.0.32"
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
WORKDIR /stub
# --chmod: в рабочей копии файлы бывают 600, а COPY сохраняет права — пользователь app не прочтёт
COPY --chmod=0444 server.py ./server.py
USER app
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["python", "/stub/server.py"]
