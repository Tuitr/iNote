# libs/contracts — модели стыков

Каждый модуль — один стык, владелец модуля — владелец стыка. `__init__.py` без реэкспортов:
`from contracts.reply import Reply`. Примеры JSON — в `contracts/examples/` в корне репо, тесты сверяют
модели с ними (модель → JSON = пример).

| Модуль | Стык | Владелец → потребитель | Статус |
|---|---|---|---|
| `reply.py` | API агента: `MessageRequest`, `ActionRequest` → `Reply` | C → C (бот) | черновик к G1 |
| `start_param.py` | J6 bot → max_app: `parseStartParam`, `StartParam`, `StartParamStr` | C → E | черновик к G1 |
| `notify.py` | J4 scheduler → bot: `NotifyRequest`, `NotifyDelivered`, `chat_not_found` | C ← D (вызывает D) | черновик к G1, финал — G3 |
| `agent_side.py` | J2 agent ↔ Brain: `TurnInput`, `ToolExecutor`, `ToolResult` (C); `TurnResult`, `Clarify`, `Brain` — набросок, **владелец B** | C ↔ B | черновик к G1 |

Схемы всех моделей и каталог примеров — под снапшотами (`tests/test_snapshots.py`, маркер `contract`):
снапшот обновляется только при намеренной смене контракта — `uv run pytest -m contract --snapshot-update`
вместе с `contracts/examples`. Ручная проверка J4: `contracts/examples/notify/notify.sh` (нужен `SERVICE_TOKEN`).

Изменение контракта — по правилу из CLAUDE.md: PR без реализации → аппрув потребителя → реализация.
