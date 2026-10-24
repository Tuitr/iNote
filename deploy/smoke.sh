#!/usr/bin/env bash
# Смоук стенда S2 (нужен `make up`, BOT_MAX_MODE=fake): четыре обязательных сценария ТЗ голосом
# через фейковый MAX (/dev/max/voice → ASR-заглушка → агент → MCP D) и один текстом:
#   1) «Напомни завтра в 10 позвонить Ивану» → «Распознал: …» + «Напоминание создано»;
#   2) «Встреча с Иваном завтра в 15» → «Встреча создана»;
#   3) «Что у меня завтра?» → список на завтра;
#   4) «Найди звонок Ивану» → «Нашёл:»;
#   и текстом «Что у меня сегодня?» → список на сегодня.
# Работает и в режиме webhook. Путь запроса — `make logs TRACE=<trace_id из логов бота>`.
# Полная проверка (кнопки, уточнения, конфликт, no_speech) — `make e2e`.
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

# Примечание: голосовое через фейковый MAX: текст станет «аудио», ASR-заглушка его вернёт
voice() {
  python3 -c 'import json, sys; print(json.dumps({"max_user_id": int(sys.argv[2]), "max_chat_id": int(sys.argv[2]), "text": sys.argv[1]}, ensure_ascii=False))' "$1" "$2" \
    | curl -fsS -X POST "$BOT_URL/dev/max/voice" -H 'content-type: application/json' -d @- >/dev/null
}

# Примечание: ждём ответ бота в чат; печатаем первую строку ответа по существу
# (у голосового первая строка — «Распознал: …», ожидаемое ищем в начале любой строки)
expect() {
  local chat="$1" expected="$2" name="$3"
  for _ in $(seq 1 75); do
    if line="$(curl -fsS "$BOT_URL/dev/max/sent" | python3 -c '
import json, sys
chat, expected = int(sys.argv[1]), sys.argv[2]
for message in json.load(sys.stdin)["messages"]:
    if message["chat_id"] != chat:
        continue
    lines = message["text"].splitlines()
    found = [line for line in lines if line.startswith(expected)]
    if found:
        print(found[0]); break
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

BASE_CHAT=$((900000000 + RANDOM * 10))
voice "Напомни завтра в 10 позвонить Ивану" $((BASE_CHAT + 1))
voice "Встреча с Иваном завтра в 15" $((BASE_CHAT + 2))
voice "Что у меня завтра?" $((BASE_CHAT + 3))
# поиск — в том же чате, где создано напоминание: на стенде настоящий D, у нового пользователя
# записей нет; апдейты одного пользователя бот обрабатывает по очереди
voice "Найди звонок Ивану" $((BASE_CHAT + 1))
update "Что у меня сегодня?" $((BASE_CHAT + 5)) \
  | curl -fsS -X POST "$BOT_URL/dev/max/updates" -H 'content-type: application/json' -d @- >/dev/null

expect $((BASE_CHAT + 1)) "Напоминание создано" "голосом: напомни завтра в 10"
expect $((BASE_CHAT + 2)) "Встреча создана" "голосом: встреча завтра в 15"
expect $((BASE_CHAT + 3)) "Завтра, " "голосом: что у меня завтра"
expect $((BASE_CHAT + 1)) "Нашёл:" "голосом: найди звонок Ивану"
expect $((BASE_CHAT + 5)) "Сегодня, " "текстом: что у меня сегодня"
