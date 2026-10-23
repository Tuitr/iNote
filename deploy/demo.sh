#!/usr/bin/env bash
# Демо зоны C на поднятом стенде mock (make up): диалог в фейковом MAX, напечатанный как чат.
# Сценарий и паузы — в deploy/demo.py; SERVICE_TOKEN для уведомления J4 берётся из .env.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python deploy/demo.py "$@"
