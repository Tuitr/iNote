#!/usr/bin/env bash
# Распознать свой OGG/Opus сервисом asr поднятого стенда (make asr-try FILE=voice.ogg).
#   ./deploy/asr_try.sh voice.ogg docker compose -f deploy/docker-compose.yml --env-file .env --profile full
# Файл уходит в контейнер asr через stdin, запрос — изнутри контейнера на его же /v1/transcribe
# (наружу asr не опубликован), с SERVICE_TOKEN из окружения контейнера. С PROFILE=full это
# настоящий запрос к SpeechKit — платно. Ответ J1 (или конверт ошибки) печатается как есть.
# Записать голосовое на Mac: ffmpeg -f avfoundation -i ":0" -t 5 -ac 1 -c:a libopus voice.ogg
# Из заметки «Диктофон»:      ffmpeg -i memo.m4a -ac 1 -c:a libopus -b:a 24k voice.ogg
set -euo pipefail

FILE=${1:-}
shift || true
if [[ -z "$FILE" || ! -f "$FILE" ]]; then
  echo "нужен файл: make asr-try FILE=voice.ogg [PROFILE=full]" >&2
  exit 2
fi
if [[ $# -eq 0 ]]; then
  echo "нужна команда compose после имени файла (её подставляет make)" >&2
  exit 2
fi

read -r -d '' SEND <<'PY' || true
import json, os, sys
import httpx
audio = sys.stdin.buffer.read()
response = httpx.post(
    "http://127.0.0.1:8000/v1/transcribe",
    files={"audio": ("voice.ogg", audio, "audio/ogg")},
    data={"lang": "ru-RU"},
    headers={"Authorization": "Bearer " + os.environ["SERVICE_TOKEN"]},
    timeout=20,
)
print(response.status_code)
print(json.dumps(response.json(), ensure_ascii=False, indent=2))
PY

"$@" exec -T asr python -c "$SEND" < "$FILE"
