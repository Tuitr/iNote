"""Конфиг стенда текстом: Caddy не открывает служебное, наружу публикуется только Caddy.

Проверка без Docker и без сети — по файлам deploy/Caddyfile и deploy/docker-compose.stand.yml.
"""

import re
from pathlib import Path
from typing import Final

import pytest

DEPLOY: Final = Path(__file__).resolve().parents[2] / "deploy"
CADDYFILE: Final = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
# Наружу — только эти сервисы (webhook, API мини-приложения, статика)
ALLOWED_UPSTREAMS: Final = {"bot:8000", "api:8000", "max_app:8000"}


# Примечание: Caddyfile без комментариев — чтобы слова в пояснениях не путали проверки
def caddyDirectives() -> list[str]:
    lines = (line.split("#", 1)[0].strip() for line in CADDYFILE.splitlines())
    return [line for line in lines if line]


# Примечание: блоки сервисов docker-compose.stand.yml текстом: имя → строки блока (без YAML-
# библиотеки: у PyYAML нет типов, а формат файла простой — два пробела на уровень)
def standServices() -> dict[str, list[str]]:
    text = (DEPLOY / "docker-compose.stand.yml").read_text(encoding="utf-8")
    services: dict[str, list[str]] = {}
    current: str | None = None
    inServices = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inServices = line.rstrip() == "services:"
            current = None
            continue
        if inServices and (match := re.fullmatch(r"  (\w+):", line.rstrip())):
            current = match[1]
            services[current] = []
        elif inServices and current is not None:
            services[current].append(line.strip())
    return services


# Примечание: значение KEY: … в блоке сервиса
def setting(lines: list[str], key: str) -> str:
    [value] = [line.split(":", 1)[1].strip() for line in lines if line.startswith(f"{key}:")]
    return value


# Примечание: закрытые пути отсекаются 404 до любого reverse_proxy
@pytest.mark.parametrize("prefix", ["/internal", "/dev", "/health", "/v1", "/mcp", "/metrics"])
def test_privatePathsBlockedFirst(prefix: str) -> None:
    directives = caddyDirectives()
    [private] = [d for d in directives if d.startswith("@private path ")]
    assert f"{prefix} " in f"{private} "
    assert f"{prefix}/* " in f"{private} "
    respond = directives.index("respond @private 404")
    firstProxy = next(i for i, d in enumerate(directives) if d.startswith("reverse_proxy"))
    assert respond < firstProxy


# Примечание: проксируется только бот (webhook), api (/api/v1/*) и статика max_app
def test_onlyAllowedUpstreams() -> None:
    upstreams = {d.split()[-1] for d in caddyDirectives() if d.startswith("reverse_proxy")}
    assert upstreams == ALLOWED_UPSTREAMS
    for internal in ("agent", "mcp", "asr", "redis", "postgres", "scheduler"):
        assert not re.search(rf"\b{internal}:\d+", CADDYFILE)


# Примечание: к боту — только POST /webhook/max; к api — только /api/v1/*
def test_botAndApiNarrow() -> None:
    directives = caddyDirectives()
    assert "reverse_proxy @webhook bot:8000" in directives
    assert "request_body @webhook {" in directives
    assert "max_size 1MB" in directives
    assert "method POST" in directives
    assert "path /webhook/max" in directives
    assert "@api path /api/v1/*" in directives
    assert "reverse_proxy @api api:8000" in directives
    assert "respond @apiOther 404" in directives
    assert directives.index("respond @apiOther 404") < directives.index(
        "reverse_proxy max_app:8000"
    )


# Примечание: на стенде наружу публикуется только Caddy; бот — webhook и настоящий MAX
def test_standComposePublishesOnlyCaddy() -> None:
    services = standServices()
    assert set(services) == {"caddy", "bot", "api", "agent", "asr", "max_app", "backup"}
    assert "ports:" not in services["agent"]
    assert (
        setting(services["agent"], "AGENT_BRAIN"),
        setting(services["asr"], "ASR_PROVIDER"),
    ) == (
        "llm",
        "yandex",
    )
    assert "ports:" in services["caddy"]
    # бот и api в стенде разработки опубликованы на 127.0.0.1, на стенде с HTTPS — только Caddy
    assert setting(services["bot"], "ports") == "!reset []"
    assert setting(services["api"], "ports") == "!reset []"
    bot = services["bot"]
    assert (setting(bot, "BOT_UPDATES_MODE"), setting(bot, "BOT_MAX_MODE")) == ("webhook", "real")
    url = setting(bot, "BOT_WEBHOOK_URL")
    assert url.startswith("https://")
    assert url.endswith("/webhook/max")
    assert setting(bot, "BOT_WEBHOOK_SECRET").startswith("${BOT_WEBHOOK_SECRET:?")


# Примечание: секреты в файлах стенда — только ссылками на переменные окружения
def test_noSecretsInStandFiles() -> None:
    text = (DEPLOY / "docker-compose.stand.yml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "SECRET" in line or "TOKEN" in line:
            value = line.split(":", 1)[1] if ":" in line else ""
            assert "${" in value or not value.strip() or line.lstrip().startswith("#")
