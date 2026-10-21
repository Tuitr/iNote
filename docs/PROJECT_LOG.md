# iNote — журнал проекта (зона D)

Правила работы — в инструкциях проекта GorkyCode и в CLAUDE.md Никиты. План команды лежит в
[`docs/plan/`](plan/): «Команда и протоколы 2.md» (дальше КиП) и «D · MCP БД API Scheduler.md» (план D).
Черновика D (`predlozheniya-D.md`) в репо пока нет; до G1 Никита считает его контрактом для заглушек.

---

## Часть 1. Текущее состояние проекта

### Обзор

iNote — голосовой ассистент задач и встреч: бот в MAX + мини-приложение. Пользователь говорит
или пишет боту, речь распознаётся (A), агент с LLM (B, C) вызывает MCP-инструменты (D),
данные лежат в PostgreSQL (D), мини-приложение (E) показывает календарь через API (D).

Сейчас в репо этап S0 зоны D: каркас и заглушки, по которым другие роли могут начинать работу.

- `libs/contracts` — модели стыков: ошибки, `Item`, `ItemBrief`, `UserSettings`, аргументы и ответы
  8 MCP-инструментов (J3), тела запросов и ответов API (J5).
- `contracts/examples/` — JSON-примеры J3 и J5; `contracts/openapi/api.json` — OpenAPI для E.
- `mcp` — `/health` и MCP-заглушка на `/mcp`: 8 инструментов проверяют аргументы и отвечают примерами.
- `api` — `/health` и заглушка `/v1`: отвечает примерами J5, умеет ETag/304.
- `compose.yaml` — postgres (pgvector), redis, mcp, api.

Настоящей логики (запись в БД, поиск, напоминания) ещё нет: это S1–S3.

### Архитектура

```mermaid
flowchart LR
    agent["agent (C)"] -->|"MCP /mcp (J3): заглушка"| mcp
    agent -.->|"/internal/v1 (J8): ещё нет"| mcp
    max_app["max_app (E)"] -->|"/v1 (J5): заглушка"| api
    subgraph D["Зона D, один образ"]
        mcp["mcp :8000"]
        api["api :8000"]
        core["libs/core"]
        contracts["libs/contracts + contracts/examples"]
        mcp --> core
        api --> core
        mcp --> contracts
        api --> contracts
    end
    core --> pg[("PostgreSQL + pgvector")]
    core --> redis[("Redis")]
```

Сейчас `libs/core` только проверяет PostgreSQL и Redis для `/health/ready`. Заглушки берут ответы из
`contracts/examples/`, а схемы — из моделей `libs/contracts`, поэтому пример, модель и схема не расходятся.
Принцип (решение Никиты): логика в `libs/core`, сервисы — тонкие адаптеры.

### Структура репозитория

Полный черновик для всей команды: [`docs/STRUCTURE_DRAFT.md`](STRUCTURE_DRAFT.md). Что есть сейчас:

| Путь | За что отвечает | Зачем так | Кто пользуется |
| --- | --- | --- | --- |
| `libs/contracts/` | Модели стыков между ролями | По КиП каждый стык описан кодом в одном месте | все роли |
| `libs/contracts/errors.py` | 10 кодов ошибок, их HTTP-статусы, конверт ошибки для HTTP (с `trace_id`) и для MCP (без него) | Один формат ошибок на весь проект (КиП) | D отдаёт, C и E читают |
| `libs/contracts/health.py` | Формат ответа `/health/ready` | Формат задан в КиП | D, позже все сервисы |
| `libs/contracts/items.py` | `Item`, `ItemBrief`, `kind`, `status` | Общая модель для J3, J4, J5 | D отдаёт; C и E читают |
| `libs/contracts/settings.py` | `UserSettings`, тип `IanaTimezone` | Одна модель для J8, `/v1/me` и `TurnInput.settings` (черновик D) | D отдаёт; C и E читают |
| `libs/contracts/tools.py` | Аргументы и ответы 8 инструментов, `Caller`, реестр `TOOLS` | Схемы инструментов — контракт J3; из них строится `list_tools` | D; C (агент), B (eval) |
| `libs/contracts/api.py` | Тела запросов и ответов `/v1` | Контракт J5; из них FastAPI строит OpenAPI | D; E |
| `contracts/examples/j3/` | По файлу на инструмент: `arguments`, `structuredContent`, `text`; плюс пример ошибки | Ответы MCP-заглушки и образец для B и C (КиП) | B, C |
| `contracts/examples/j5/` | Тела запросов и ответов API | Ответы заглушки API и примеры в OpenAPI | E |
| `contracts/openapi/api.json` | OpenAPI, выгруженный из `api` | E генерирует из него типы и моки (КиП, J5) | E |
| `libs/core/` | Вся логика бэкенда D | Решение Никиты: логика отдельно от транспорта | `mcp`, `api`, позже `scheduler` |
| `libs/core/config.py` | Чтение и проверка переменных окружения | Сервис падает при старте с понятной ошибкой | `services/http_common.py` |
| `libs/core/resources.py` | Подключения к PostgreSQL и Redis | Одно место для драйвера asyncpg и пула | `services/http_common.py` |
| `libs/core/health.py` | Проверки PostgreSQL и Redis с таймаутом | Не зависит от HTTP, пригодится и scheduler | `/health/ready` |
| `libs/core/validation.py` | Ошибки pydantic → `details.missing` / `details.invalid` | Одинаковый ответ на неверные поля в MCP и API, без значений полей | `mcp`, `api` |
| `libs/core/log_setup.py` | Настройка стандартного `logging` | Временно, до `libs/common` (C) со structlog | `services/http_common.py` |
| `services/http_common.py` | Общий lifespan (подключения) и роутер `/health` | Без двух одинаковых копий; временно до `libs/common` | `api`, `mcp` |
| `services/mcp/app.py` | Приложение `mcp`: `/health` + смонтированный MCP на `/mcp` | КиП: MCP на `http://mcp:8000/mcp`, рядом будет `/internal/v1` | контейнер `mcp`, C |
| `services/mcp/tools/stub.py` | MCP-заглушка: 8 инструментов на `Server` из SDK `mcp` 2.x | C и B подключаются до реализации; удаляется в S1 | C, B |
| `services/api/app.py` | Приложение `api`: `/health` + `/v1` | Отдельный контейнер `api` | контейнер `api`, E |
| `services/api/errors.py` | `ApiError` и перевод ошибок в общий конверт, `trace_id` | Все ошибки API в формате КиП; 400 вместо стандартного 422 FastAPI | `api` |
| `services/api/routes/stub.py` | Заглушка `/v1`: отвечает примерами J5 | E работает до реализации; удаляется в S2 | E |
| `services/api/export_openapi.py` | Выгрузка OpenAPI в `contracts/openapi/api.json` | Файл в репо — контракт для E; тест ловит невыгруженные изменения | D |
| `*/tests/` | Тесты рядом с кодом | Видно, что чем проверено | `pytest` |
| `Dockerfile` | Один образ на блок D: только код D, контракты и примеры | Решение Никиты (развилка А) | compose |
| `compose.yaml` | postgres (pgvector), redis, mcp, api | Шаг 2 S0; общий с C | все |
| `pyproject.toml` | Зависимости, сборка, ruff, pytest | Один файл вместо `requirements.txt` + конфигов | D |
| `.env.example` | Переменные окружения без значений | Правило «секреты только в `.env`» | все |
| `.gitignore`, `.dockerignore` | Не коммитить `.env`, `.venv`, `.idea`; не тащить тесты и docs в образ | Секреты и мусор не попадают в git и образ | git, Docker |
| `docs/plan/` | Копии файлов плана команды | Агенты и люди читают план из репо | все |
| `docs/BACKEND_D_EXPLAINED.md` | Описание зоны D простым языком: что сделано, сценарии пользователя, зачем каждый файл | Чтобы новый человек понял бэкенд без чтения кода | все |
| `docs/STRUCTURE_DRAFT.md` | Черновик структуры монорепо и CODEOWNERS | Для G0 | вся команда |
| `docs/PROJECT_LOG.md` | Этот журнал | Единый источник правды по зоне D | все |

### Как запустить и проверить

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m pytest -q                 # ожидается: 105 passed
.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check .
.venv/Scripts/python -m services.api.export_openapi   # после любого изменения API или libs/contracts/api.py
```

Запуск без Docker (PostgreSQL и Redis заглушкам не нужны, только `/health/ready` тогда ответит 503):

```bash
DATABASE_URL=postgresql://user:password@localhost:5432/inote REDIS_URL=redis://localhost:6379/0 \
  .venv/Scripts/uvicorn services.mcp.app:app --port 8001
curl localhost:8001/health/live    # 200 {"status":"ok"}
```

Проверка MCP-заглушки настоящим клиентом SDK:

```python
import asyncio
from mcp import Client


async def main() -> None:
    async with Client("http://localhost:8001/mcp") as client:
        print([tool.name for tool in (await client.list_tools()).tools])
        caller = {
            "user_id": 17,
            "tz": "Europe/Moscow",
            "trace_id": "ab12f0c4d5e6f708192a3b4c5d6e7f80",
        }
        result = await client.call_tool(
            "create_task", {"caller": caller, "title": "Позвонить Ивану"}
        )
        print(result.content[0].text, result.structured_content)


asyncio.run(main())
```

Заглушка API: `uvicorn services.api.app:app --port 8000` с теми же переменными, затем
`curl "localhost:8000/v1/items?from=2026-10-19&to=2026-10-22&group=day" -H "Authorization: Bearer x"`.
Документация Swagger: `http://localhost:8000/docs`.

Docker (не проверено: на машине разработчика нет Docker). Скопировать `.env.example` в `.env`, задать пароль, затем:

```bash
docker compose up --build
curl localhost:8000/health/ready   # api
curl localhost:8001/health/ready   # mcp
```

### Зависимости

| Библиотека | Зачем | Откуда |
| --- | --- | --- |
| fastapi, uvicorn | HTTP-сервисы `mcp` и `api` | план D |
| pydantic v2 | модели контрактов | план D |
| sqlalchemy[asyncio] 2, asyncpg | подключение к PostgreSQL | план D |
| redis (redis-py, asyncio) | подключение к Redis | план D |
| mcp 2.x | MCP-сервер на Streamable HTTP | план D; версию 2.x выбрал Никита 2026-10-20 |
| tzdata | база часовых поясов для `zoneinfo` | одобрил Никита 2026-10-20 |
| pytest | тесты | план D |
| ruff | линтер и форматтер | правила Никиты |
| httpx | нужен `fastapi.testclient`, только dev | одобрил Никита 2026-10-20 |

Образы в compose: `pgvector/pgvector:pg16` (план D), `redis:7-alpine` (в плане не указан, выбран D).
Python 3.12 в Docker (план D); локально работает и на 3.13.

### Соглашения

- Импорт от корня репо: `libs.core...`, `libs.contracts...`, `services.mcp...`. Имя `mcp` занято MCP SDK.
- `DATABASE_URL` в обычной схеме `postgresql://`; драйвер asyncpg подставляет `libs/core/resources.py`.
- Отсутствующая обязательная переменная окружения → `ConfigError` при старте.
- Ошибки не глотаются: ловятся конкретные типы, в лог пишется только тип, без строки подключения.
- Неверные поля → `validation_error` с `details.missing` (не передано) и `details.invalid` (не прошло проверку), только имена полей.
- Схемы инструментов и OpenAPI строятся из моделей `libs/contracts`. Примеры проверяются тестами против моделей,
  `contracts/openapi/api.json` — против выгрузки. Поменял контракт и не обновил пример или OpenAPI — тест красный (КиП).
- Тексты и комментарии на русском, поэтому в ruff отключены RUF001–RUF003.

---

## Часть 2. Журнал решений

| Дата | Решение | Альтернативы | Почему | Кто утвердил |
| --- | --- | --- | --- | --- |
| 2026-10-20 | Структуру всего репо даём черновиком (`docs/STRUCTURE_DRAFT.md`), зону D делаем сразу | Ждать G0 | Не блокироваться; по КиП структуру утверждает команда на G0 | Никита |
| 2026-10-20 | Модели контрактов кладём сразу в `libs/contracts` | Сначала в `libs/core` | Никита сам согласует их с командой | Никита |
| 2026-10-20 | Типы `Item` берём из черновика D | Ждать согласования | В КиП только JSON-пример без типов | Никита |
| 2026-10-20 | В шаг 0 не входят «схема БД v0 + EMBED_DIM» и «список инструментов v0» из чек-листа S0 плана D | Делать сейчас | Схема зависит от A (EMBED_DIM), инструменты — от C и G1 | Никита |
| 2026-10-20 | Логи — стандартный `logging`, не structlog | structlog сейчас | КиП требует structlog, но через `libs/common` (зона C), которого ещё нет. Свой structlog в D потом пришлось бы выкидывать | агент, Никита разрешил решить самому |
| 2026-10-20 | `UserSettings` пока не делаем | Сделать по черновику D | В плане противоречие: в J5 `tz` рядом с `settings`, в J8 и черновике D — внутри | агент (заменено решением ниже) |
| 2026-10-20 | Один `Dockerfile` на блок D, сервисы различаются командой запуска | По образу на сервис | Решение Никиты (развилка А) | Никита, C ещё подтвердить |
| 2026-10-20 | Общие lifespan и `/health` для `mcp` и `api` вынесены в `services/http_common.py` | Копия в каждом `app.py` | Две одинаковые копии по 40 строк; когда C выложит `libs/common` с health-роутером, удаляем этот файл | агент |
| 2026-10-20 | `httpx` — dev-зависимость | Не тестировать HTTP-слой | Без него не работает `fastapi.testclient`; в рантайм не попадает | Никита |
| 2026-10-20 | `ruff` — dev-зависимость в `pyproject.toml` | Ставить глобально | Одна версия у всех, правило Никиты «ruff перед коммитом» | агент |
| 2026-10-20 | Таймаут проверки готовности 2 секунды | Без таймаута | Иначе `/health/ready` зависает при недоступной БД | агент |
| 2026-10-20 | `UserSettings` делаем по черновику D: `tz` внутри настроек | `tz` рядом с `settings`, как в J5 | Решение Никиты; противоречие J5/J8 он выносит команде | Никита |
| 2026-10-20 | У `tz` в модели нет значения по умолчанию | Читать `DEFAULT_TZ` в модели | Модель контракта не должна зависеть от окружения; `DEFAULT_TZ` подставит бэкенд при создании пользователя | агент |
| 2026-10-20 | `tzdata` — рантайм-зависимость | `apt install tzdata` в Docker; не проверять пояс | Одинаково работает на Windows и в Docker; черновик D требует `validation_error` на неизвестный пояс | Никита |
| 2026-10-20 | `Dockerfile` копирует только код D и `libs/contracts`, а не все `libs/` и `services/` | Копировать папки целиком | Иначе код других ролей попадёт в образ D, когда они начнут писать в общие папки | агент, по вопросу Никиты |
| 2026-10-20 | Доделать S0: шаги 2 (Compose), 5 (MCP-заглушка), 6 (OpenAPI + заглушка api); для заглушек черновик D = контракт | Ждать G1 | «Остальные зависят от меня и подстраиваются» | Никита |
| 2026-10-20 | MCP SDK `mcp` 2.x | 1.x `FastMCP` (план D) | Текущая ветка SDK, отвечает клиентам на старой и новой версии протокола (черновик D) | Никита |
| 2026-10-20 | В заглушке используется низкоуровневый `Server` из `mcp` 2.x, а не обёртка `MCPServer` | `MCPServer` с функцией на каждый инструмент | `MCPServer` строит схему из параметров функции: пришлось бы второй раз перечислять все поля из `libs/contracts/tools.py`, и схема могла бы разойтись с контрактом. `Server` берёт схему прямо из моделей. Пакет и версия те же | агент, **нужно подтверждение Никиты** |
| 2026-10-20 | Схемы инструментов и OpenAPI строятся из pydantic-моделей `libs/contracts`, примеры лежат в JSON и проверяются тестами | Писать схемы и примеры руками | Одно место правды; КиП требует, чтобы изменение контракта без обновления примера давало красный тест | агент |
| 2026-10-20 | Неверные поля → `details.missing` и `details.invalid` (только имена) | Только `missing`, как в черновике D | Черновик D задаёт только `missing`; `invalid` — предложение D для остальных ошибок | агент, вынести на G1 |
| 2026-10-20 | Описаний инструментов в заглушке нет | Написать свои | По КиП тексты описаний пишет B | агент |
| 2026-10-20 | `Me` = `{name, settings}`, `tz` внутри `settings` | `{name, tz, settings}`, как в таблице J5 | Решение Никиты взять `UserSettings` из черновика D | Никита (противоречие с J5 выносит сам) |
| 2026-10-20 | `PATCH /v1/me` принимает плоское подмножество полей настроек и возвращает `Me` | `{"settings": {...}}` | Формат тела в КиП не описан; плоский — как тело `PATCH …/settings` в J8 | агент, вынести на G1 |
| 2026-10-20 | Заглушка API принимает любой Bearer-токен, без токена — 401 | Без проверки; настоящий JWT | Фронт с первого дня ходит с `Authorization` и обрабатывает 401; подпись `initData` и JWT — S2 | агент |
| 2026-10-20 | В заглушке API нет `/v1/dialog`, `PATCH`/`DELETE /v1/items/{id}` и `restore` | Сделать по таблице J5 | По КиП формат истории и запись из приложения решают на G3 | агент |
| 2026-10-20 | Ошибки валидации API — 400 `validation_error` в общем конверте | Стандартный 422 FastAPI | КиП: `validation_error` = 400, 422 занят `past_time` | агент |
| 2026-10-20 | В compose пока нет `migrate` и `scheduler` | Добавить пустые | Им нечего запускать: Alembic появится в S1, Scheduler в S3 | агент |
| 2026-10-20 | `compose.yaml` лежит в корне | `deploy/compose.yaml` | `docker compose up` работает без флагов; место решается на G0 | агент |
| 2026-10-20 | В Docker пакет ставится через `pip install -e .` | Обычная установка | Код остаётся в `/app`, пути к `contracts/examples` одинаковые локально и в образе, нет второй копии кода | агент |
| 2026-10-20 | Пока один `pyproject.toml` в корне, отдельный для `libs/contracts` не делаем | Свой `pyproject.toml` у каждой библиотеки и сервиса | Вернёмся, когда другим ролям понадобится импортировать `libs/contracts` в свои образы | Никита |

---

## Часть 3. Отчёты по шагам

### Шаг 0 (S0, каркас зоны D) — 2026-10-20

#### 1. Что сделано

Сделан каркас зоны D: структура папок, `pyproject.toml`, `Dockerfile`, `.env.example`. В `libs/contracts`
описаны согласованные в КиП коды ошибок и конверт ошибки, а также `Item`, `ItemBrief` и `UserSettings` (v0 по черновику D). Сервисы `mcp` и `api`
отвечают на `/health/live` и `/health/ready` в формате КиП. Для команды подготовлен черновик структуры всего монорепо.

#### 2. Изменённые файлы

| Файл | Назначение |
| --- | --- |
| `pyproject.toml` | зависимости, сборка пакетов `libs*`/`services*`, ruff, pytest |
| `.gitignore`, `.dockerignore` | не коммитить `.env`, `.venv`, `.idea`; не тащить лишнее в образ |
| `.env.example` | `DATABASE_URL`, `REDIS_URL`, `LOG_LEVEL` |
| `Dockerfile` | образ блока D на `python:3.12-slim`, запуск не от root |
| `libs/contracts/errors.py` | `ErrorCode`, `HTTP_STATUS_BY_CODE`, `ErrorInfo`, `HttpErrorResponse`, `ToolErrorResponse` |
| `libs/contracts/health.py` | `ReadinessResponse` |
| `libs/contracts/items.py` | `ItemKind`, `ItemStatus`, `ItemBrief`, `Item` |
| `libs/contracts/settings.py` | `UserSettings`, тип `IanaTimezone` |
| `libs/core/config.py` | `Settings`, `load_settings()`, `ConfigError` |
| `libs/core/resources.py` | `create_db_engine()`, `create_redis_client()` |
| `libs/core/health.py` | `check_postgres()`, `check_redis()`, `check_readiness()` |
| `libs/core/log_setup.py` | `configure_logging()` |
| `services/http_common.py` | `lifespan`, `health_router` |
| `services/api/app.py`, `services/mcp/app.py` | приложения FastAPI |
| `libs/contracts/tests/*`, `libs/core/tests/*`, `services/tests/*` | 44 теста |
| `docs/STRUCTURE_DRAFT.md` | черновик структуры монорепо и CODEOWNERS для G0 |
| `docs/PROJECT_LOG.md` | этот журнал |

#### 3. Как это работает

1. uvicorn запускает `services/api/app.py:app` (или `mcp`). Перед первым запросом FastAPI вызывает `lifespan`
   из `services/http_common.py`.
2. `lifespan` читает окружение через `load_settings()` (`libs/core/config.py`). Нет `DATABASE_URL` или `REDIS_URL`,
   или `LOG_LEVEL` не из списка — `ConfigError`, сервис не стартует.
3. `lifespan` создаёт движок БД (`create_db_engine`) и клиент Redis (`create_redis_client`) и кладёт их в `app.state`.
   Подключение при этом ещё не открывается, только при первом запросе. При остановке оба закрываются (блок `finally`).
4. `GET /health/live` сразу отвечает `200 {"status":"ok"}`: процесс жив.
5. `GET /health/ready` вызывает `check_readiness()` (`libs/core/health.py`): параллельно (`asyncio.gather`) делает
   `SELECT 1` в PostgreSQL и `PING` в Redis, каждую проверку не дольше 2 секунд. Обе `ok` → 200 и `status: ok`,
   иначе 503 и `status: degraded` со списком проверок.

Конструкции Python, которые здесь встречаются:
- **`async def` / `await`** — асинхронная функция: пока ждёт ответа БД, сервер обслуживает другие запросы.
- **`@asynccontextmanager` + `yield`** в `lifespan` — код до `yield` выполняется при старте, после — при остановке.
- **`async with asyncio.timeout(...)`** — если блок не закончился за время, внутри бросается `TimeoutError`.
- **`asyncio.gather(a, b)`** — запустить две проверки одновременно и дождаться обеих.
- **`@dataclass(frozen=True)`** — класс-контейнер для полей, после создания менять нельзя.
- **Pydantic `BaseModel`** — класс, который проверяет данные при создании: `Item.model_validate(json)` бросит
  `ValidationError`, если тип не тот. `AwareDatetime` принимает только время со смещением (правило КиП).
- **`StrEnum`** — перечисление, значения которого — строки (`ItemKind.EVENT == "event"`).
- **`str | None`** — поле может быть строкой или `null`.
- **`Annotated[str, AfterValidator(f)]`** — тип «строка, которую после обычной проверки дополнительно проверяет функция `f`». Так сделан `IanaTimezone` в `libs/contracts/settings.py`.
- **`Field(ge=5, le=480)`** — число от 5 до 480 включительно, иначе `ValidationError`.

#### 4. Ключевые места в коде

- `libs/contracts/errors.py`, `HTTP_STATUS_BY_CODE` — таблица «код → HTTP-статус» из КиП. Тест
  `test_every_error_code_has_http_status` не даст добавить код без статуса.
- `libs/contracts/errors.py`, `HttpErrorInfo.trace_id` — проверка формата «32 hex-символа» (КиП) регуляркой.
- `libs/contracts/items.py`, `Item(ItemBrief)` — `Item` наследует `ItemBrief`, потому что краткая форма —
  ровно подмножество полей. Так поля не дублируются.
- `libs/core/resources.py`, `create_db_engine` — драйвер asyncpg подставляется в коде, чтобы `DATABASE_URL`
  был обычным `postgresql://` и подходил другим ролям. `pool_pre_ping` — чтобы после перезапуска PostgreSQL
  первый запрос не падал на порванном соединении.
- `libs/contracts/settings.py`, `_require_iana_timezone` — пояс проверяется через `ZoneInfo(name)`: если такого пояса нет в базе `tzdata`, будет ошибка. `ValueError` тоже ловится: так `ZoneInfo` отвечает на строки вроде `../etc/passwd`.
- `libs/core/health.py` — в лог пишется только тип ошибки, а не её текст: в тексте может оказаться строка
  подключения с паролем.

#### 5. Результаты код-ревью

Найдено и исправлено:
- Одинаковые `lifespan` и `/health` в двух `app.py` → вынесены в `services/http_common.py`.
- Неверный `LOG_LEVEL` ронял сервис непонятной ошибкой `logging` → проверка в `load_settings()` с понятным текстом.
- Неточный комментарий в тесте (на Windows порт 1 даёт таймаут, а не отказ) → исправлен.
- Не было комментария, зачем `pool_pre_ping` → добавлен.

Осознанно оставлено:
- `services/http_common.py`, логирование и конфиг пересекаются с будущим `libs/common` зоны C. Пока его нет,
  без них `mcp` и `api` не запустить. Удалим, когда C выложит `libs/common`.
- `app.state.db_engine` и `app.state.redis_client` без типов (так устроен `app.state` в FastAPI). Обращение к ним только в одном месте.
- Общий корневой `pyproject.toml`: зависимости всех ролей попадут в образ D, если другие роли допишут свои туда же. Решается на G0 (вопрос 2 в `STRUCTURE_DRAFT.md`): свой `pyproject.toml` у каждой роли.
- В `Dockerfile` исходники копируются и устанавливаются как пакет (две копии в образе). Это несколько килобайт, зато команда запуска одинаковая локально и в Docker.
- `UserSettings.tz` обязателен, без значения по умолчанию: `DEFAULT_TZ` подставит бэкенд при создании пользователя (следующие шаги).
- `details` в ошибке по умолчанию `{}`: в КиП пример всегда с объектом, обязательность не указана.

#### 6. Известные ограничения и техдолг

- **`docker build` не проверен**: на машине нет Docker.
- **Путь «всё ok» у `/health/ready` проверен только с подменой** (`services/tests`), живых PostgreSQL и Redis не было.
  Путь «всё упало» проверен вживую: `mcp` запущен, `/health/live` → 200, `/health/ready` → 503.
- Тесты недоступной БД идут ~8 секунд на Windows (ждут таймаут 2 с).
- `fastapi.testclient` предупреждает, что `httpx` устарел для него и советует `httpx2`.
- Нет полей логов из КиП (`service`, `trace_id`, `user_id`, `duration_ms`) и `X-Trace-Id`: это `libs/common` (C).
- Ограничения `Item` (title 1–200, nullable полей) и всё `UserSettings` — v0 по черновику D, команда не согласовала.
- `remind_before_min`: в `UserSettings` 0–1440, а в аргументах `create_event` черновика D просто «≥ 0». Сделано 0–1440; при описании инструментов надо выровнять.
  Длины `notes`, `source_text`, `participants`, `rrule` не ограничены: в плане их нет.
- `/health/ready` пока не проверяет MCP SDK и миграции: их ещё нет.
- По КиП у каждой зоны должен быть README с абзацем про сделанное. Пока его роль играет этот журнал.

#### 7. Что дальше

Нужно для следующего шага:
- **C:** compose (postgres `pgvector/pgvector:pg16`, redis, migrate, mcp, api, scheduler из одного образа),
  подтверждение «один образ на блок D», версия MCP SDK (план D: FastMCP 1.x, черновик D: `mcp` 2.x `MCPServer`),
  `libs/common`.
- **E:** согласие на формат OpenAPI v0 для J5.
- **A:** `EMBED_DIM` для схемы БД v0.
- **Вся команда (G0):** `docs/STRUCTURE_DRAFT.md`, владелец `Item`, где лежит `tz` (J5 рядом с `settings`, J8 и код — внутри).

Открытых вопросов к Никите по шагу 0 нет. Следующий шаг по плану D — схема БД v0 и Alembic, для него нужен `EMBED_DIM` от A.

### Шаг 0, часть 2 (S0: шаги 2, 5, 6) — 2026-10-20

#### 1. Что сделано

Доделан этап S0 зоны D. Есть `compose.yaml` с postgres, redis, mcp и api. На `/mcp` работает MCP-заглушка:
8 инструментов отдают схемы из `libs/contracts`, проверяют аргументы и отвечают готовыми примерами.
На `/v1` работает заглушка API для мини-приложения, а OpenAPI выгружен в `contracts/openapi/api.json`.
Теперь C может подключить агента к MCP, а E — сгенерировать типы из OpenAPI (критерий готовности S0 в плане D).

#### 2. Изменённые файлы

| Файл | Назначение |
| --- | --- |
| `compose.yaml` | postgres (`pgvector/pgvector:pg16`), redis, mcp (порт 8001), api (порт 8000), healthcheck у всех |
| `.env.example` | добавлены `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` для контейнера postgres |
| `Dockerfile` | копирует `contracts/examples`, ставит пакет через `pip install -e .` |
| `pyproject.toml` | зависимость `mcp>=2.3,<3` |
| `libs/contracts/tools.py` | аргументы и ответы 8 инструментов, `Caller`, `DateOnly`, `DateOrDateTime`, `ItemRef`, реестр `TOOLS` |
| `libs/contracts/api.py` | `AuthMaxRequest`, `AuthMaxResponse`, `Me`, `MePatch`, `ItemsByDayResponse`, `ItemsResponse` |
| `libs/core/validation.py` | `validation_error_details()`: ошибки pydantic → `missing` / `invalid` |
| `contracts/examples/j3/*.json` | 8 примеров инструментов + `error_past_time.json` |
| `contracts/examples/j5/*.json` | 9 примеров запросов и ответов API |
| `contracts/openapi/api.json` | выгруженный OpenAPI |
| `services/mcp/tools/stub.py` | `build_stub_server()`, `load_examples()` |
| `services/mcp/app.py` | `create_app()`: `/health` + MCP на `/mcp` |
| `services/api/errors.py` | `ApiError`, `install_error_handlers()` |
| `services/api/routes/stub.py` | заглушки `/v1/auth/max`, `/v1/me` (GET, PATCH), `/v1/items`, `/v1/items/search`, `/v1/items/{id}` |
| `services/api/app.py` | подключены обработчики ошибок и заглушка `/v1` |
| `services/api/export_openapi.py` | выгрузка OpenAPI |
| `libs/contracts/tests/test_tools.py`, `test_api.py` | примеры J3 и J5 совпадают с моделями, проверки дат и id |
| `services/tests/test_mcp_stub.py` | заглушка через настоящий клиент SDK (в памяти) |
| `services/tests/test_api_stub.py` | эндпоинты заглушки, 401, 400, 404, ETag/304, актуальность OpenAPI |
| `docs/plan/` | копии «Команда и протоколы 2.md» и «D · MCP БД API Scheduler.md» |

#### 3. Как это работает

**MCP-заглушка** (`services/mcp/tools/stub.py`, `services/mcp/app.py`):
1. При старте `create_app()` читает 8 примеров из `contracts/examples/j3` (`load_examples`) и собирает MCP-сервер
   (`build_stub_server`). Сервер монтируется в FastAPI на `/mcp`; `/health` остаётся рядом.
2. На `list_tools` сервер отдаёт 8 инструментов. Схема аргументов — `model_json_schema()` модели из `TOOLS`,
   схема ответа — так же. Поэтому агент C видит ровно то, что описано в `libs/contracts/tools.py`.
3. На `call_tool` сервер находит модель аргументов, проверяет ими `arguments`. Не прошло — ответ `isError: true`
   с `validation_error` и именами полей (`details.missing` / `details.invalid`). Прошло — ответ из примера:
   `content[0].text` (строка для модели) и `structuredContent` (JSON для агента), как в J3.
4. Неизвестный инструмент — `isError: true` с `not_found`.
5. В `lifespan` запускается менеджер сессий MCP (`session_manager.run()`): без него Streamable HTTP не принимает запросы.
6. Защита SDK от DNS rebinding по умолчанию пускает только localhost. В `_TRANSPORT_SECURITY` добавлен хост `mcp`,
   иначе агент из сети compose (`http://mcp:8000/mcp`) получил бы отказ.

**Заглушка API** (`services/api/routes/stub.py`, `services/api/errors.py`):
1. `POST /v1/auth/max` проверяет только, что `init_data` не пустой, и отдаёт пример с токеном.
2. Остальные эндпоинты подключены к `protected`: зависимость `require_token` требует заголовок
   `Authorization: Bearer …` (любой), иначе `ApiError(unauthorized)` → 401.
3. `GET /v1/items` отдаёт пример по дням при `group=day` или плоский список. В заголовке `ETag` — версия (`"42"`).
   Если пришёл `If-None-Match` с той же версией — 304 без тела (КиП, J5).
4. `GET /v1/items/{id}` отдаёт пример, если id совпал, иначе 404 `not_found`. Некорректный UUID — 400.
5. `install_error_handlers` переводит `ApiError` и ошибки валидации FastAPI в общий конверт КиП. `trace_id` берётся
   из `X-Trace-Id`, если он корректный, иначе создаётся новый: по КиП API начинает цепочку для запросов фронта.
6. Примеры из `contracts/examples/j5` попадают и в ответы, и в OpenAPI (`responses=...`), поэтому моки E совпадут с заглушкой.

Конструкции Python, которые здесь встречаются:
- **`@dataclass(frozen=True)`** у `ToolExample` — неизменяемый контейнер для примера.
- **`Annotated[date, BeforeValidator(f)]`** у `DateOnly` — перед обычной проверкой даты вызывается `f`: она отсекает
  строки со временем, которые pydantic иначе принял бы за дату.
- **`Field(alias="from")`** — в Python поле зовётся `from_` (`from` — ключевое слово), а в JSON и OpenAPI — `from`.
- **`model_fields_set`** у `UpdateItemArgs` — какие поля реально пришли: так `null` («очистить») отличается от «не передано».
- **`Depends(require_token)`** — FastAPI вызывает `require_token` перед каждым эндпоинтом роутера `protected`.
- **`async with a, b:`** в `services/mcp/app.py` — два контекстных менеджера сразу: подключения к БД и сессии MCP.
- **Замыкания** `list_tools` и `call_tool` внутри `build_stub_server` видят `examples` и `tool_specs` из внешней функции.

#### 4. Ключевые места в коде

- `libs/contracts/tools.py`, `_ITEM_REF` — регулярка с `^…$`. Без якорей pydantic ищет совпадение в любом месте строки,
  и `../7f3a9c2e` прошёл бы как id. Нашёл это тестом `test_malformed_id_is_rejected`.
- `libs/contracts/tools.py`, `_require_date_only` — `2026-10-18T00:00:00` без смещения pydantic принимает как дату,
  а по КиП время без смещения — `validation_error`.
- `libs/contracts/tools.py`, `TOOLS` — единственный список инструментов. Его используют заглушка (`list_tools`, проверка
  аргументов) и тесты (есть ли пример на каждый инструмент).
- `services/mcp/tools/stub.py`, `_list_tool_specs` — описаний нет намеренно: тексты пишет B.
- `services/api/export_openapi.py` — `newline="\n"`, иначе на Windows файл запишется с `\r\n` и не совпадёт на Linux.
- `services/tests/test_api_stub.py`, `test_committed_openapi_is_up_to_date` — поменяли API и не выгрузили OpenAPI → тест красный.

#### 5. Результаты код-ревью

Найдено и исправлено:
- Регулярка id без якорей пропускала мусор → добавлены `^` и `$`.
- Одинаковая сборка `details.missing` / `details.invalid` в MCP и API → вынесена в `libs/core/validation.py`.
- `create_app()` вместо глобального приложения в `mcp`: менеджер сессий MCP можно запустить только один раз,
  а тестам нужно несколько приложений.
- OpenAPI на Windows писался с `\r\n` → `newline="\n"`.
- `ToolExample` был классом с ручным `__init__` → `dataclass`.

Осознанно оставлено:
- Параметры эндпоинтов заглушки (`from_`, `to`, `kind`, `q`, `limit`, тела запросов) не используются в коде. Они нужны,
  чтобы FastAPI проверял запрос и показывал параметры в OpenAPI — это и есть контракт для E.
- Заглушки не смотрят на аргументы и всегда отдают один пример. Это заглушки, логика будет в S1–S2.
- Нет проверки `SERVICE_TOKEN` на `/mcp`: по КиП это middleware из `libs/common` (C), его ещё нет.

#### 6. Известные ограничения и техдолг

- **Не проверено:** `docker build` и `docker compose up` (нет Docker); генерация типов E из OpenAPI
  (`openapi-typescript` не запускал).
- Проверено вживую: `mcp` поднят через uvicorn, настоящий клиент SDK получил 8 инструментов, вызвал `create_task`
  и получил ошибку валидации; `api` отдал пример по дням, 401 без токена и 304 по ETag.
- В примере `items_by_day` напоминание без `starts_at` лежит в дне своего `remind_at`. Как группировать такие записи,
  в КиП не сказано — предложение D.
- `MePatch`: `null` у поля значит «не менять», очистить настройку нельзя. Для настроек это, видимо, и не нужно, но в КиП не описано.
- `/internal/v1` (J8) не сделан: в шаги 5 и 6 он не входит. По КиП для J8 тоже нужна заглушка на FastAPI к G1.
- Снапшот-тесты сделаны сравнением с файлами, без `syrupy` из плана D: хватило стандартного pytest.
- В логах нет `trace_id` (требование КиП к готовой задаче): это structlog из `libs/common` (C).

#### 7. Что дальше

- **Никите:** подтвердить `Server` вместо `MCPServer`; посмотреть предложения D для G1 (`details.invalid`, тело
  `PATCH /v1/me`, группировка напоминаний по дням).
- **C:** подключить агента к `http://mcp:8000/mcp`; согласовать `compose.yaml`; `libs/common` (токен, логи, `X-Trace-Id`).
- **B:** тексты описаний 8 инструментов и формат строк `text`.
- **E:** сгенерировать типы из `contracts/openapi/api.json`.
- **Следующий шаг D:** заглушка J8 или S1 (Alembic + схема БД v0, нужен `EMBED_DIM` от A; без него можно начать TimeService).
