#!/usr/bin/env bash
# Смоук стенда: подложить апдейт фейковому MAX и дождаться эха. Нужен `make up` (BOT_MAX_MODE=fake).
set -euo pipefail

BOT_URL="${BOT_URL:-http://127.0.0.1:${BOT_DEV_PORT:-8001}}"
FIXTURE="${FIXTURE:-services/bot/tests/fixtures/max_updates/text.json}"
EXPECTED="${EXPECTED:-Вы написали: }"

curl -fsS -X POST "$BOT_URL/dev/max/updates" -H 'content-type: application/json' -d @"$FIXTURE" >/dev/null
for _ in $(seq 1 50); do
  if curl -fsS "$BOT_URL/dev/max/sent" | grep -q "$EXPECTED"; then
    echo "smoke ok: бот ответил через фейковый MAX"
    exit 0
  fi
  sleep 0.2
done
echo "smoke FAIL: эхо не пришло за 10 с; см. make logs SERVICE=bot" >&2
exit 1
