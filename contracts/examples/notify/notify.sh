#!/usr/bin/env bash
# J4: отправить уведомление боту, как это делает Scheduler (D). Повтор с тем же notification_id
# должен вернуть duplicate=true. Нужны SERVICE_TOKEN и доступ к боту (локально — make up, порт 8001).
#   SERVICE_TOKEN=... ./contracts/examples/notify/notify.sh [request.json]
set -euo pipefail

BOT_URL="${BOT_URL:-http://127.0.0.1:${BOT_DEV_PORT:-8001}}"
REQUEST="${1:-$(dirname "$0")/request.json}"
TRACE_ID="${TRACE_ID:-$(openssl rand -hex 16)}"

: "${SERVICE_TOKEN:?задайте SERVICE_TOKEN (тот же, что в .env)}"
curl -sS -X POST "$BOT_URL/internal/v1/notify" \
  -H "Authorization: Bearer $SERVICE_TOKEN" \
  -H "X-Trace-Id: $TRACE_ID" \
  -H 'content-type: application/json' \
  -d @"$REQUEST" \
  -w '\nHTTP %{http_code}\n'
echo "trace_id=$TRACE_ID — ищите в make logs TRACE=${TRACE_ID:0:6}"
