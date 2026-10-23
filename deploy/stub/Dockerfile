# Заглушка чужого сервиса для compose S0: отвечает на /health/live и /health/ready.
# Только stdlib — образ не зависит от кода других зон. После мержа заменить на build: services/<имя>.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
# --chmod: в рабочей копии файлы бывают 600, а COPY сохраняет права — пользователь app не прочтёт
WORKDIR /stub
COPY --chmod=0444 server.py ./server.py
USER app
EXPOSE 8000
STOPSIGNAL SIGTERM
CMD ["python", "-u", "/stub/server.py"]
