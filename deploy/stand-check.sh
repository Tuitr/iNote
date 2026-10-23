#!/usr/bin/env bash
# Проверка .env перед стендом с HTTPS (make stand-config / make deploy): без заглушечных значений
# стенд наружу не поднимаем. Значения не печатаем — только имена переменных.
set -euo pipefail

ENV_FILE="${1:-.env}"
[ -f "$ENV_FILE" ] || { echo "stand-check: нет $ENV_FILE" >&2; exit 1; }

# Примечание: значение переменной из .env (последнее, без кавычек)
value() {
  grep -E "^$1=" "$ENV_FILE" | tail -1 | cut -d= -f2- | sed -e 's/^"//' -e 's/"$//'
}

fail=0
token="$(value SERVICE_TOKEN)"
if [ "${#token}" -lt 32 ] || [[ "$token" == change-me* ]]; then
  echo "stand-check: SERVICE_TOKEN — заглушка или короче 32 символов (openssl rand -hex 32)" >&2
  fail=1
fi
secret="$(value BOT_WEBHOOK_SECRET)"
if ! [[ "$secret" =~ ^[a-zA-Z0-9_-]{16,256}$ ]]; then
  echo "stand-check: BOT_WEBHOOK_SECRET — нужен 16–256 символов a-zA-Z0-9_- (openssl rand -hex 24)" >&2
  fail=1
fi
for name in STAND_DOMAIN ACME_EMAIL; do
  if [ -z "$(value "$name")" ]; then
    echo "stand-check: пусто $name" >&2
    fail=1
  fi
done
[ "$fail" -eq 0 ] && echo "stand-check: .env готов для стенда"
exit "$fail"
