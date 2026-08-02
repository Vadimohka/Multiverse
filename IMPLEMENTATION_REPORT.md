# Итоговый отчёт — Parser Studio

Дата проверки: 30 июля 2026 года.

## Результат

Собран интегрированный, запускаемый **single-tenant MVP конструктора парсинга финансовых сайтов, API и документов**. Это не только архитектурный макет: workflow, созданный в UI или через API, исполняется единым backend engine, сохраняет raw artifacts и результаты узлов, создаёт versioned records, diff/review tasks и экспортирует подтверждённые данные.

## Основные компоненты

- `apps/frontend` — React, TypeScript, Vite, React Flow, русский UI.
- `apps/api` — FastAPI `/api/v1`, JWT/RBAC, CRUD, execution orchestration и SSE.
- `apps/worker` — Celery workers: general, browser, document, LLM и Beat.
- `packages/workflow_engine` — DAG engine, каталог из 26 исполняемых узлов и нормализаторы финансовых данных.
- `migrations` — Alembic: initial schema, schedule state и LLM-call history.
- `tests` — unit и API integration tests.
- `docker-compose.yml` — PostgreSQL, Redis, MinIO, API, frontend, четыре workers и Beat.

## Интегрированные функции

- JWT login/refresh и роли Administrator/Developer/Operator/Viewer.
- Проекты, источники, Source Profiler, CAPTCHA/JS/table/document/XHR diagnostics.
- Source-to-workflow wizard.
- React Flow editor с node catalog, inspector, typed config fields, JSON/operations/field mapping editors, Condition ports, save/validate/publish/run и node test.
- Selector snapshot: screenshot, DOM bounding boxes, CSS/XPath candidates и static fallback.
- HTTP, Playwright, browser actions, rendered HTML, screenshot и network capture.
- PDF/DOCX/XLSX/CSV/JSON parser; Docling/Tesseract в document worker image.
- DeepSeek/OpenAI-compatible provider abstraction, mock, JSON schema validation и LLM-call journal.
- Safe transforms/formulas, financial rate/currency/term normalization, validation, deduplication и condition branches.
- Dataset persistence: natural keys, hashes, record versions, confidence, diff и review policy.
- Review Queue: approve, reject и correction с корректным переключением current version.
- Raw-first storage в MinIO/S3 с SHA-256 и локальным fallback.
- External DB insert/upsert, field mapping, allowed tables и webhook.
- XLSX/CSV/JSON export.
- Cron schedules, Celery routing, run retry/cancel, node history и SSE status events.
- Audit log, dashboard, Prometheus-compatible metrics, health/readiness и structured request logs.

## Выполненные проверки

| Проверка | Результат |
|---|---|
| Python `compileall` | Успешно |
| Pytest | **21 passed** |
| Alembic на чистой SQLite БД | `0003_llm_calls (head)` |
| Frontend production build | `tsc -b && vite build` успешно |
| Docker Compose YAML parse | Успешно, 10 сервисов |
| Docker Compose configuration | `docker compose config` успешно |
| Docker Compose runtime | 10 сервисов подняты одновременно; API, PostgreSQL, Redis и MinIO healthy |
| Frontend Docker image | Собран успешно |
| Реальный healthcheck Compose API | HTTP 200 |
| Full demo workflow | Успешно |
| Node runs demo workflow | 7/7 SUCCESS |
| Извлечённые записи | 3 |
| Review tasks | 3, затем все APPROVED |
| Raw artifacts | Созданы |
| XLSX export | Валидный ZIP/XLSX, 6505 байт в smoke-run |
| Metrics endpoint | `runs_total` и `review_queue_size` доступны |
| Workflow node catalog | 26 узлов |
| Browser acceptance check | Login, dashboard, 13 основных разделов, document-source wizard, validate workflow, interactive selector screenshot/overlay и logout успешно |

Полную проверку можно повторить командой:

```bash
python scripts/smoke_test.py
```

## Команда запуска

```bash
cp .env.example .env
docker compose up --build
```

URL: `http://localhost:8080`

Начальные credentials из `.env.example` (до первого production-развёртывания их нужно заменить):

```text
admin@parser.local
ChangeThisDemoAdminPassword123!
```

Их необходимо заменить перед размещением в сети.

## Границы фактической проверки

- Стек из десяти сервисов поднят и прошёл healthcheck. Основной demo workflow, async worker, MinIO raw artifacts, document worker и реальный минимальный DeepSeek request проверены в runtime.
- Внешние PostgreSQL/MySQL и произвольные внешние сайты не использовались для приёмочного прогона, чтобы не записывать данные во внешние системы и не зависеть от их состояния.
- `npm audit` сообщает о двух high-уязвимостях upstream React Router. Доступный registry не публикует исправленную версию; приложение является Vite SPA и не использует React Server Components.

## Известные продуктовые границы

MVP не является полной enterprise-реализацией всех пунктов исходного мастер-ТЗ. В частности, отсутствуют multi-tenant isolation, noVNC-интерактивная авторизация, email trigger/output, самостоятельный GraphQL builder, визуальные join/group/fuzzy/SCD2-конструкторы и подтверждённые нагрузочные параметры для 10 млн записей.

Ключевой пользовательский сценарий статических финансовых сайтов — URL → profiler → visual workflow → selectors → normalization → validation → versioned dataset → review → XLSX — интегрирован и проходит smoke-test. Playwright, documents, LLM и external outputs включены как рабочие узлы и требуют соответствующего production runtime.
