#!/usr/bin/env bash
# Смоук стенда S1 (нужен `make up`, BOT_MAX_MODE=fake): два сценария через фейковый MAX —
#   1) «Что у меня сегодня?» → список на сегодня по шаблону агента;
#   2) «Напомни завтра в 10 позвонить Ивану» → подтверждение из ответа MCP-заглушки.
# Путь запроса — `make logs TRACE=<код из ответа или trace_id из логов бота>`. Полная проверка — `make e2e`.
set -euo pipefail

BOT_URL="${BOT_URL:-http://127.0.0.1:${BOT_DEV_PORT:-8001}}"
RUN_ID="$(date +%s)$$"

# Примечание: апдейт MAX со своим чатом и уникальным mid (повторный прогон не отсекается дедупом)
update() {
  python3 - "$1" "$2" "$RUN_ID" <<'PY'
import json, sys, time
text, chat, run = sys.argv[1], int(sys.argv[2]), sys.argv[3]
now = int(time.time() * 1000)
print(json.dumps({
    "update_type": "message_created",
    "timestamp": now,
    "message": {
        "sender": {"user_id": chat, "first_name": "Smoke", "is_bot": False},
        "recipient": {"chat_id": chat, "chat_type": "dialog"},
        "timestamp": now,
        "body": {"mid": f"mid.smoke.{run}.{chat}", "text": text},
    },
    "user_locale": "ru",
}, ensure_ascii=False))
PY
}

# Примечание: ждём ответ бота в чат; печатаем первую строку ответа
expect() {
  local chat="$1" expected="$2" name="$3"
  for _ in $(seq 1 75); do
    if line="$(curl -fsS "$BOT_URL/dev/max/sent" | python3 -c '
import json, sys
chat, expected = int(sys.argv[1]), sys.argv[2]
for message in json.load(sys.stdin)["messages"]:
    if message["chat_id"] == chat and message["text"].startswith(expected):
        print(message["text"].splitlines()[0]); break
else:
    sys.exit(1)
' "$chat" "$expected")"; then
      echo "smoke ok: $name → «$line»"
      return 0
    fi
    sleep 0.2
  done
  echo "smoke FAIL: $name — нет ответа «$expected…» за 15 с; см. make logs SERVICE=bot и SERVICE=agent" >&2
  return 1
}

TODAY_CHAT=$((900000000 + RANDOM))
REMIND_CHAT=$((TODAY_CHAT + 1))
update "Что у меня сегодня?" "$TODAY_CHAT" \
  | curl -fsS -X POST "$BOT_URL/dev/max/updates" -H 'content-type: application/json' -d @- >/dev/null
update "Напомни завтра в 10 позвонить Ивану" "$REMIND_CHAT" \
  | curl -fsS -X POST "$BOT_URL/dev/max/updates" -H 'content-type: application/json' -d @- >/dev/null

expect "$TODAY_CHAT" "Сегодня, " "что у меня сегодня"
expect "$REMIND_CHAT" "Напоминание создано" "напомни завтра в 10"
