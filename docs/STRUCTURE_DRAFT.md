# Черновик структуры монорепо iNote

> Статус: **черновик для встречи G0**, не утверждён командой.
> Подготовил: роль D (Никита) с помощью агента, 2026-10-20.
> По плану структуру репо, CODEOWNERS и стандарты утверждает вся команда на G0,
> итог переезжает в `docs/standards.md`. До этого всё ниже — предложение.
> Источник зон и имён: «Команда и протоколы» (КиП) и планы ролей A–E. Пути папок сервисов
> в плане не указаны, есть только имена контейнеров, поэтому пути `services/<контейнер>/` — предложение.

## Принципы

1. **Один репозиторий, папка = зона ответственности.** По папке видно владельца, CODEOWNERS пишется по папкам.
2. **Стыки J1–J8 описаны кодом в `libs/contracts/`** и примерами в `contracts/examples/` (КиП). Сервисы импортируют модели оттуда, а не копируют.
3. **Библиотеки в `libs/`, запускаемые сервисы в `services/`.** Папка сервиса называется как его контейнер в compose (`asr`, `agent`, `bot`, `mcp`, `api`, `scheduler`, `max_app`).
4. **Один `pyproject.toml` в корне на весь Python-код** с общей настройкой ruff и pytest. Если чьи-то зависимости мешают другим (ffmpeg у A, SDK LLM у B), роль заводит свой `pyproject.toml` в своей папке. Решаем на G0.
5. **Тесты рядом с кодом**: `<пакет>/tests/`. Наборы данных для eval и ASR — в `tests/` в корне, как в планах A и B.
6. Ветки `feat/<зона>-<что>`, PR в `main` с ревью хотя бы одного человека (КиП).

## Дерево

```text
iNote/
├── libs/
│   ├── contracts/              # ОБЩЕЕ. Pydantic-модели стыков J1–J8
│   │   ├── errors.py           # конверт ошибки и коды (D, готово)
│   │   ├── health.py           # ответ /health/ready (D, готово)
│   │   ├── items.py            # Item, ItemBrief (D, v0 по черновику D)
│   │   ├── settings.py         # UserSettings (D, v0 по черновику D)
│   │   ├── tools.py            # J3: аргументы и ответы 8 инструментов (D, v0)
│   │   ├── api.py              # J5: тела запросов и ответов /v1 (D, v0)
│   │   ├── asr.py              # J1 (A)
│   │   └── ...                 # J2–J8 у владельцев стыков
│   ├── common/                 # C. Конфиг, логи (structlog), lifecycle, health-роутер,
│   │                           #    HTTP-клиент, middleware X-Trace-Id и SERVICE_TOKEN
│   ├── brain/                  # B. Промпт, цикл инструментов, LLM-клиент
│   └── core/                   # D. Логика бэкенда: БД, репозитории, сервисы
│       ├── config.py           # переменные окружения D
│       ├── resources.py        # подключения к PostgreSQL и Redis
│       ├── health.py           # проверки PostgreSQL и Redis
│       ├── log_setup.py        # настройка logging (до libs/common)
│       ├── validation.py       # ошибки pydantic → details.missing / invalid
│       ├── db/                 # (позже) модели SQLAlchemy
│       ├── repositories/       # (позже) доступ к таблицам, всегда с user_id
│       └── services/           # (позже) сценарии: создать, найти, список
├── services/
│   ├── asr/                    # A. POST /v1/transcribe (J1), /v1/synthesize (опция)
│   ├── agent/                  # C. Agent Back: /v1/messages, /v1/actions, McpToolExecutor
│   ├── bot/                    # C. Max bot: /webhook/max, /internal/v1/notify (J4)
│   ├── mcp/                    # D. MCP на /mcp (J3) + служебный REST /internal/v1 (J8)
│   │   ├── app.py
│   │   └── tools/              # 8 инструментов, описания пишет B; сейчас stub.py — заглушка
│   ├── api/                    # D. REST /v1 для mini app (J5)
│   │   ├── errors.py           # общий конверт ошибки, trace_id
│   │   ├── routes/             # сейчас stub.py — заглушка /v1
│   │   └── export_openapi.py   # выгрузка contracts/openapi/api.json
│   ├── scheduler/              # D. напоминания и сдвиг серий, вызывает J4
│   ├── http_common.py          # D. lifespan и /health для mcp и api (до libs/common)
│   └── tests/                  # D. тесты HTTP-слоя mcp и api
├── max_app/                    # E. Мини-приложение: TypeScript, React 18, Vite
├── migrations/                 # D. (позже) Alembic, вместе со схемой БД v0
├── contracts/
│   ├── examples/               # ОБЩЕЕ. JSON-примеры по папке на стык: j3/, j5/ (D), j1/ (A)…
│   └── openapi/api.json        # D + E. OpenAPI для J5
├── tests/
│   ├── asr/samples.yaml        # A. эталоны голосовых
│   └── eval/cases.yaml         # B. кейсы eval
├── deploy/                     # C. Caddy, настройки стенда
├── compose.yaml                # C + D. Сейчас: postgres, redis, mcp, api
├── docs/
│   ├── standards.md            # итог G0
│   ├── redis-keys.md           # C. реестр ключей Redis (J7)
│   ├── PROJECT_LOG.md          # журнал зоны D
│   ├── STRUCTURE_DRAFT.md      # этот файл
│   └── plan/                   # копии файлов плана команды
├── Dockerfile                  # D. один образ на блок D (mcp, api, scheduler, migrate)
├── pyproject.toml              # Python-зависимости, ruff, pytest
├── .env.example                # все переменные окружения без значений
└── .github/
    ├── CODEOWNERS
    └── workflows/              # C. GitHub Actions
```

`(позже)` — папок ещё нет в репо, они появятся вместе с первым кодом.

## Черновик CODEOWNERS

Строки с пометкой «КиП» взяты из плана, остальные предложены по таблице зон.

```text
/libs/contracts/          @A @B @C @D @E   # КиП: владелец стыка + потребитель
/services/mcp/tools/      @D @B            # КиП
/libs/brain/              @B               # КиП
/docs/redis-keys.md       @C               # КиП
/contracts/openapi/       @D @E            # КиП
/contracts/examples/      @A @B @C @D @E
/libs/common/             @C
/libs/core/               @D
/services/asr/            @A
/services/agent/          @C
/services/bot/            @C
/services/mcp/            @D
/services/api/            @D
/services/scheduler/      @D
/services/http_common.py  @D
/services/tests/          @D
/migrations/              @D
/max_app/                 @E
/tests/asr/               @A
/tests/eval/              @B
/deploy/                  @C
/compose.yaml             @C @D
/Dockerfile               @D
```

Для `libs/contracts/` можно точнее, по файлу на стык: `asr.py` → A и C, `items.py` → D, C, E и т. д.

## Вопросы на G0

1. Подходят ли пути `services/<контейнер>/` и `max_app/` для фронта.
2. Один `pyproject.toml` на всё или свой у каждой роли.
3. Кто владелец общих моделей `Item`/`ItemBrief`: они нужны J3 (D), J4 (C) и J5 (D).
4. Где compose. Сейчас `compose.yaml` в корне, чтобы `docker compose up` работал без флагов.
5. Python-пакет `mcp` занят MCP SDK, поэтому код D импортируется как `services.mcp`, а не `mcp`. Если остальные сервисы тоже импортируются как `services.<имя>`, конфликтов не будет.
6. `libs/common` (C) и временный код D: D сейчас сам читает переменные окружения, настраивает logging и отдаёт /health. Когда C выложит `libs/common`, D переходит на него и удаляет своё.
