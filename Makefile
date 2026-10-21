# Makefile монорепо. Python-команды — через uv (см. docs/decisions.md).
SHELL := /bin/bash
.DEFAULT_GOAL := help
UV ?= uv

# --- зона C ---
.PHONY: help install hooks fmt lint typecheck imports test test-unit test-contract \
        test-integration test-lifecycle cov check

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
	$(UV) run mypy libs services

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
# --- конец зоны C ---
