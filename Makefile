# Makefile монорепо. Python-команды — через uv (см. docs/decisions.md).
SHELL := /bin/bash
.DEFAULT_GOAL := help
UV ?= uv

# --- зона C ---
.PHONY: help install hooks fmt lint typecheck imports test test-unit test-contract \
        test-integration test-lifecycle cov check up down logs ps smoke e2e seed eval deploy \
        stand-config redis-check demo env-docs asr-try eval-fake

# Стенд: docker compose из deploy/, переменные — из .env (создаётся из .env.example)
PROFILE ?= mock
COMPOSE ?= docker compose -f deploy/docker-compose.yml --env-file .env --profile $(PROFILE)
UP_FLAGS ?= --build
SERVICE ?=
TRACE ?=
MAX_USER_ID ?=
# ASR по профилю: mock — фейк без ключей и денег, full — SpeechKit (ключ ASR_YANDEX_API_KEY из .env).
# export: значение видит docker compose (подстановка ${ASR_PROVIDER}) и перекрывает строку из .env
ASR_PROVIDER_mock := fake
ASR_PROVIDER_full := yandex
export ASR_PROVIDER := $(ASR_PROVIDER_$(PROFILE))
# Brain по профилю: mock — правила без LLM (бесплатно, e2e), full — GigaChat (ключ LLM_GIGACHAT_CREDENTIALS)
AGENT_BRAIN_mock := scripted
AGENT_BRAIN_full := llm
export AGENT_BRAIN := $(AGENT_BRAIN_$(PROFILE))
LLM_PROVIDER_mock := fake
LLM_PROVIDER_full := gigachat
export LLM_PROVIDER := $(LLM_PROVIDER_$(PROFILE))
FILE ?=

help: ## список целей
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

install: ## зависимости всех пакетов workspace (по uv.lock)
	$(UV) sync --all-packages --frozen

hooks: install ## поставить git-хуки pre-commit и commit-msg
	$(UV) run pre-commit install

fmt: ## автоформат и автоисправления ruff
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

lint: ## ruff без правок
	$(UV) run ruff check .
	$(UV) run ruff format --check .

typecheck: ## mypy --strict
	$(UV) run mypy libs services tests

imports: ## границы слоёв и пакетов
	$(UV) run lint-imports

test: test-unit ## unit + контракты (быстрые)

test-unit: ## unit + contract
	$(UV) run pytest -m "not integration and not lifecycle and not e2e"

test-contract: ## только снапшоты контрактов
	$(UV) run pytest -m contract || test $$? -eq 5  # 5 = тестов с маркером пока нет

test-integration: ## нужен Docker
	$(UV) run pytest -m integration || test $$? -eq 5

test-lifecycle: ## SIGTERM, старт и остановка
	$(UV) run pytest -m lifecycle || test $$? -eq 5

cov: ## покрытие сервисов зоны C (порог 60%)
	$(UV) run pytest -m "not e2e" --cov --cov-fail-under=60

check: ## всё, что гоняет pre-commit, по всем файлам
	$(UV) run pre-commit run --all-files

.env:
	cp .env.example .env
	@echo "создан .env из .env.example — поменяйте SERVICE_TOKEN"

up: .env ## поднять стенд (PROFILE=mock|full) и дождаться healthy всех сервисов
	$(COMPOSE) up -d $(UP_FLAGS) --wait
	@$(COMPOSE) ps --format 'table {{.Service}}\t{{.Status}}\t{{.Ports}}'

down: ## остановить стенд (данные Postgres в volume сохраняются)
	$(COMPOSE) down

ps: ## статус контейнеров
	$(COMPOSE) ps

logs: ## логи: SERVICE=bot — один сервис; TRACE=<trace_id или первые 6> — путь запроса
ifeq ($(TRACE),)
	$(COMPOSE) logs -f --tail=200 $(SERVICE)
else
	$(COMPOSE) logs --no-color $(SERVICE) | grep -- "$(TRACE)"
endif

smoke: ## четыре сценария ТЗ голосом и один текстом через фейковый MAX (нужен make up)
	./deploy/smoke.sh

e2e: up ## e2e на стенде: tests/e2e (поднимает стенд, если ещё не поднят), затем аудит Redis
	$(UV) run pytest -m e2e tests/e2e
	$(UV) run python deploy/redis_check.py $(COMPOSE)

env-docs: ## таблицы переменных в README бота и агента — из классов настроек
	$(UV) run python deploy/env_docs.py

demo: ## демо на поднятом стенде mock: диалог в фейковом MAX как чат (DEMO_PAUSE_S — пауза)
	./deploy/demo.sh

redis-check: ## аудит Redis поднятого стенда: ключи из реестра, TTL, maxmemory и политика
	$(UV) run python deploy/redis_check.py $(COMPOSE)

asr-try: ## распознать свой OGG/Opus сервисом asr стенда: FILE=voice.ogg (PROFILE=full — SpeechKit, платно)
	./deploy/asr_try.sh "$(FILE)" $(COMPOSE)

seed: ## демо-данные D на аккаунт MAX: make seed MAX_USER_ID=<id> (нужен make up)
	@test -n "$(MAX_USER_ID)" || (echo "нужен MAX_USER_ID=<id пользователя в MAX>" >&2 && exit 1)
	$(COMPOSE) run --rm mcp python -m services.seed --max-user-id $(MAX_USER_ID)

EVAL_REPEAT ?= 3
eval: .env ## eval Brain на GigaChat — платно, только по прямой просьбе владельца B (ключ из .env)
	set -a && . ./.env && set +a && LLM_PROVIDER=gigachat $(UV) run python -m brain.eval run --provider gigachat \
		--cases tests/eval/cases.yaml --repeat $(EVAL_REPEAT)

eval-fake: ## eval на оракуле: бесплатно, проверяет раннер и кейсы
	$(UV) run python -m brain.eval run --provider fake --cases tests/eval/cases.yaml --repeat 1

STAND_COMPOSE ?= docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.stand.yml \
	--env-file .env --profile full

stand-config: ## проверить .env и конфиг стенда с HTTPS (compose + Caddy) без запуска
	./deploy/stand-check.sh .env
	$(STAND_COMPOSE) config --quiet

deploy: stand-config ## стенд с HTTPS: Caddy + webhook MAX (только по прямой просьбе)
	$(STAND_COMPOSE) up -d --build --wait
# --- конец зоны C ---
