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
jwt="$(value API_JWT_SECRET)"
if [ "${#jwt}" -lt 32 ] || [[ "$jwt" == change-me* ]]; then
  echo "stand-check: API_JWT_SECRET — заглушка или короче 32 символов (openssl rand -hex 32)" >&2
  fail=1
fi
password="$(value POSTGRES_PASSWORD)"
if [ "${#password}" -lt 16 ] || [[ "$password" == change-me* ]]; then
  echo "stand-check: POSTGRES_PASSWORD — заглушка или короче 16 символов (openssl rand -hex 16)" >&2
  fail=1
fi
if [[ "$(value DATABASE_URL)" != *"$password"* ]]; then
  echo "stand-check: DATABASE_URL не содержит POSTGRES_PASSWORD — сервисы D не войдут в базу" >&2
  fail=1
fi
if [ "$(value API_DEV_AUTH)" = "true" ]; then
  echo "stand-check: API_DEV_AUTH=true — подпись initData не проверяется; на стенде false" >&2
  fail=1
fi
# профиль full: настоящие MAX, SpeechKit и GigaChat
for name in STAND_DOMAIN ACME_EMAIL BOT_MAX_TOKEN ASR_YANDEX_API_KEY ASR_YANDEX_FOLDER_ID \
  LLM_GIGACHAT_CREDENTIALS; do
  if [ -z "$(value "$name")" ]; then
    echo "stand-check: пусто $name" >&2
    fail=1
  fi
done
# API MAX и GigaChat подписаны НУЦ Минцифры — без файла бот и агент не установят TLS
CERT="$(dirname "$0")/certs/russian_trusted_root_ca.pem"
if [ ! -s "$CERT" ]; then
  echo "stand-check: нет $CERT (как получить — deploy/certs/README.md)" >&2
  fail=1
fi
[ "$fail" -eq 0 ] && echo "stand-check: .env готов для стенда"
exit "$fail"
