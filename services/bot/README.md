# services/bot — бот MAX (зона C)

Слои: `domain` → `application` (use cases и порты) ← `infrastructure` (Redis, HTTP, MAX), `presentation` (FastAPI, long polling);
`main.py` — composition root. Границы проверяет `make imports`.

Статус: каркас (S0, шаг 1), эхо-бот на фейковом MAX — шаг 3.
