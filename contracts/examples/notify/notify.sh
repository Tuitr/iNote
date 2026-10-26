#!/usr/bin/env bash
# J4: Scheduler (D) → бот, вместо настоящего Scheduler'а. Шлёт NotifyRequest с сервисным токеном
# и своим X-Trace-Id и повторяет по правилам J4: 200 — готово (sent); 404 chat_not_found — без
# повторов (failed); 429, 5xx и таймаут — повтор с растущей паузой, до 5 попыток (attempt 1…5).
#
#   SERVICE_TOKEN=... ./contracts/examples/notify/notify.sh [сценарий] [request.json]
#     once            — одно уведомление (по умолчанию)
#     repeat          — то же уведомление ещё раз → duplicate, второго сообщения нет
#     parallel        — два одновременных запроса → одно сообщение (второй — 503, потом duplicate)
#     chat-not-found  — чат «заблокирован» в фейковом MAX → 404 и ни одного повтора
#     all             — все четыре
# Нужен стенд (make up, BOT_MAX_MODE=fake). notification_id — новый на сценарий (NOTIFICATION_ID —
# свой). Путь цепочки — make logs TRACE=<trace_id>.
set -euo pipefail

BOT_URL="${BOT_URL:-http://127.0.0.1:${BOT_DEV_PORT:-8001}}"
SCENARIO="${1:-once}"
REQUEST="${2:-$(dirname "$0")/request.json}"
BACKOFF_S="${NOTIFY_BACKOFF_S:-1}"
: "${SERVICE_TOKEN:?задайте SERVICE_TOKEN (тот же, что в .env)}"

# Примечание: тело запроса: свой notification_id и номер попытки
body() {
  python3 - "$REQUEST" "$1" "$2" <<'PY'
import json, sys
raw = json.load(open(sys.argv[1], encoding="utf-8"))
raw["notification_id"], raw["attempt"] = sys.argv[2], int(sys.argv[3])
print(json.dumps(raw, ensure_ascii=False))
PY
}

# Примечание: одна попытка: печатает HTTP-код; ответ — в $OUT
attempt() {
  local id="$1" n="$2" trace="$3"
  body "$id" "$n" | curl -sS -o "$OUT" -w '%{http_code}' --max-time 10 \
    -X POST "$BOT_URL/internal/v1/notify" \
    -H "Authorization: Bearer $SERVICE_TOKEN" -H "X-Trace-Id: $trace" \
    -H 'content-type: application/json' -d @- || echo 000
}

# Примечание: как Scheduler: повтор на 429, 5xx и таймаут; 200 и 404 — конец; печатает итог
deliver() {
  local id="$1" trace="$2" status="" delay="$BACKOFF_S"
  for n in 1 2 3 4 5; do
    status="$(attempt "$id" "$n" "$trace")"
    echo "  попытка $n: HTTP $status $(cat "$OUT" 2>/dev/null | head -c 200)"
    case "$status" in
      200) RESULT=sent; return 0 ;;
      404) RESULT=failed; return 0 ;;
      429|5??|000) sleep "$delay"; delay=$((delay * 2)) ;;
      *) RESULT=failed; return 0 ;;
    esac
  done
  RESULT=failed
}

# Примечание: сколько сообщений фейковый MAX отправил в чат из запроса
sentCount() {
  local chat
  chat="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["user"]["max_chat_id"])' "$REQUEST")"
  curl -fsS "$BOT_URL/dev/max/sent" | python3 -c '
import json, sys
print(sum(1 for m in json.load(sys.stdin)["messages"] if m["chat_id"] == int(sys.argv[1])))' "$chat"
}

newId() { python3 -c 'import uuid; print(uuid.uuid4())'; }
newTrace() { openssl rand -hex 16; }
check() { if [ "$1" = "$2" ]; then echo "ok: $3"; else echo "FAIL: $3 — ждали $2, вышло $1" >&2; exit 1; fi; }

OUT="$(mktemp)"; trap 'rm -f "$OUT"' EXIT

once() {
  local id="${NOTIFICATION_ID:-$(newId)}" trace before
  trace="$(newTrace)"; before="$(sentCount)"
  echo "once: notification_id=$id trace_id=$trace"
  deliver "$id" "$trace"
  check "$RESULT" sent "уведомление доставлено"
  check "$(($(sentCount) - before))" 1 "одно сообщение в MAX"
}

repeat() {
  local id trace before
  id="$(newId)"; trace="$(newTrace)"; before="$(sentCount)"
  echo "repeat: notification_id=$id"
  deliver "$id" "$trace"; deliver "$id" "$trace"
  grep -q '"duplicate":true' "$OUT" && echo "ok: повтор — duplicate" || { echo "FAIL: нет duplicate" >&2; exit 1; }
  check "$(($(sentCount) - before))" 1 "второго сообщения нет"
}

parallel() {
  local id trace before
  id="$(newId)"; trace="$(newTrace)"; before="$(sentCount)"
  echo "parallel: notification_id=$id"
  ( OUT="$(mktemp)"; deliver "$id" "$trace"; rm -f "$OUT" ) &
  ( OUT="$(mktemp)"; deliver "$id" "$trace"; rm -f "$OUT" ) &
  wait
  check "$(($(sentCount) - before))" 1 "два одновременных запроса — одно сообщение"
}

chatNotFound() {
  local id trace chat before
  id="$(newId)"; trace="$(newTrace)"; before="$(sentCount)"
  chat="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["user"]["max_chat_id"])' "$REQUEST")"
  echo "chat-not-found: notification_id=$id"
  curl -fsS -X POST "$BOT_URL/dev/max/failures" -H 'content-type: application/json' \
    -d "{\"chat_id\": $chat, \"reason\": \"chat_not_found\", \"times\": -1}" >/dev/null
  local first; first="$(attempt "$id" 1 "$trace")"
  curl -fsS -X POST "$BOT_URL/dev/max/failures" -H 'content-type: application/json' \
    -d "{\"chat_id\": $chat, \"reason\": \"chat_not_found\", \"times\": 0}" >/dev/null
  check "$first" 404 "чат не найден — 404, Scheduler не повторяет"
  check "$(($(sentCount) - before))" 0 "сообщений нет"
}

case "$SCENARIO" in
  once) once ;;
  repeat) repeat ;;
  parallel) parallel ;;
  chat-not-found) chatNotFound ;;
  all) once; repeat; parallel; chatNotFound ;;
  *) echo "неизвестный сценарий: $SCENARIO (once | repeat | parallel | chat-not-found | all)" >&2; exit 2 ;;
esac
