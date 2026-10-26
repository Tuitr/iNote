"""Заглушка сервиса для compose S0: /health/live и /health/ready → 200, остальное → 404.

STUB_NAME и STUB_OWNER — в ответе и в логе, чтобы в `make logs` было видно, чья это заглушка.
"""

import json
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Final, override

NAME: Final = os.environ.get("STUB_NAME", "stub")
OWNER: Final = os.environ.get("STUB_OWNER", "?")
PORT: Final = int(os.environ.get("STUB_PORT", "8000"))
HEALTH_PATHS: Final = frozenset({"/health/live", "/health/ready"})


# Примечание: одна строка JSON в stdout — формат как у LOG_FORMAT=json
def log(event: str, **fields: object) -> None:
    record = {"service": NAME, "event": event, "owner": OWNER, "stub": True, **fields}
    sys.stdout.write(json.dumps(record, ensure_ascii=False) + "\n")


# Примечание: обработчик HTTP: health — 200, всё остальное — конверт ошибки not_found
class Handler(BaseHTTPRequestHandler):
    # Примечание: GET health-проб
    def do_GET(self) -> None:
        if self.path in HEALTH_PATHS:
            self._reply(200, {"status": "ok", "checks": {}, "stub": NAME, "owner": OWNER})
        else:
            self._notFound()

    # Примечание: любой POST к заглушке — 404 в стандартном конверте
    def do_POST(self) -> None:
        self._notFound()

    # Примечание: конверт ошибки по стандарту, чтобы клиенты не падали на разборе
    def _notFound(self) -> None:
        error = {"code": "not_found", "message": f"{NAME} — заглушка S0", "details": {}}
        self._reply(404, {"error": {**error, "trace_id": self.headers.get("X-Trace-Id")}})

    # Примечание: ответ с телом в формате JSON
    def _reply(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # Примечание: health-пробы не логируем — шум; остальные запросы — одной строкой
    @override
    def log_message(self, format: str, *args: object) -> None:
        if self.path not in HEALTH_PATHS:
            log("http_request", path=self.path.split("?")[0], line=format % args)


# Примечание: SIGTERM прерывает serve_forever через SystemExit — compose down без ожидания 10 с
def _onSigterm(signum: int, frame: object) -> None:
    raise SystemExit(0)


# Примечание: точка входа заглушки
def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)  # noqa: S104 — порт внутри сети compose
    signal.signal(signal.SIGTERM, _onSigterm)
    log("stub_started", port=PORT)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        log("stub_stopped")


if __name__ == "__main__":
    main()
