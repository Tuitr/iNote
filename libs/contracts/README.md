# libs/contracts — модели стыков

Каждый модуль — один стык, владелец модуля — владелец стыка. `__init__.py` без реэкспортов:
`from contracts.reply import Reply`. Примеры JSON — в `contracts/examples/` в корне репо, тесты сверяют
модели с ними (модель → JSON = пример).

| Модуль | Стык | Владелец → потребитель | Статус |
|---|---|---|---|
| `reply.py` | API агента: `MessageRequest`, `ActionRequest` → `Reply` | C → C (бот) | черновик к G1 |
| `start_param.py` | J6 bot → max_app: `parseStartParam`, `StartParam`, `StartParamStr` | C → E | черновик к G1 |
| `notify.py` | J4 scheduler → bot | C ← D | шаг 6 |
| `agent_side.py` | J2 agent ↔ Brain: сторона агента | C ↔ B | шаг 6 |

Изменение контракта — по правилу из CLAUDE.md: PR без реализации → аппрув потребителя → реализация.
