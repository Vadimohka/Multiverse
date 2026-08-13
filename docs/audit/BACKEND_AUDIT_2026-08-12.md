# Аудит бэкенда Multiverse

**Дата:** 2026-08-12  
**Область:** `apps/api`, `apps/worker`, `packages/workflow_engine`, миграции и production Compose-конфигурация.  
**Режим:** статический аудит кода и конфигурации; исходный код приложения не изменялся.

## Краткий итог

Бэкенд имеет хорошие базовые механизмы: хеширование паролей PBKDF2, шифрование секретов Fernet, scoped API tokens, версионирование записей, наблюдения по запускам, ограничения параллелизма в crawler и SQL-параметризацию через SQLAlchemy.

Но в текущем виде продукт не готов к многопользовательскому или недоверенному публичному развёртыванию. Основные причины:

1. авторизация проверяет роль, но почти нигде не проверяет принадлежность объектов проекту или пользователю;
2. workflow может выполнять запросы на произвольные URL, а preview/debug-контур получает все системные секреты и URL внешних БД;
3. нет достаточных ресурсных и сетевых границ для браузера, HTTP, документов и синхронных запусков;
4. ряд объектов разных проектов можно ошибочно связать друг с другом, а версии workflow не всегда являются неизменяемыми снимками.

Ниже приоритеты означают: **P0** — немедленно до публичного доступа, **P1** — в ближайшем security-релизе, **P2** — плановая надёжность/защита, **P3** — улучшение эксплуатации и качества.

## Найденные ошибки и уязвимости

### P0-1. Нет изоляции данных между проектами и пользователями (IDOR)

**Где:** [dependencies.py](../../apps/api/app/dependencies.py), все роутеры с `get_current_user`, в частности [projects.py](../../apps/api/app/routers/projects.py), [sources.py](../../apps/api/app/routers/sources.py), [workflows.py](../../apps/api/app/routers/workflows.py), [runs.py](../../apps/api/app/routers/runs.py), [data.py](../../apps/api/app/routers/data.py), [review.py](../../apps/api/app/routers/review.py).

**Ошибка.** `get_current_user` проверяет действующий JWT и активность пользователя, однако модели не содержат membership/tenant boundary, а запросы к объектам по ID не сопоставляют объект с разрешённым проектом. Списки проектов, источников, workflows, runs, schemas, prompts, review tasks и datasets доступны любому залогиненному пользователю. `download_artifact`, `get_run` и history записей тоже принимают только ID и проверяют лишь аутентификацию.

**Влияние.** Пользователь с ролью `VIEWER` может просмотреть данные другого проекта, его историю запусков, результаты узлов, доказательства и скачать артефакты. Пользователь с ролью Developer/Operator может менять или запускать чужие workflow, отменять/повторять runs, подтверждать review-задачи при знании ID. Это критическая утечка данных и нарушение целостности в любом multi-user сценарии.

**Как улучшить.**

- Ввести явную модель `ProjectMembership` (или tenant/organization) и project-scoped роли.
- Реализовать единые зависимости `require_project_access`, `require_source_access`, `require_workflow_access`, `require_run_access` и применять их до чтения/изменения объекта.
- Все list-запросы строить от доступных `project_id`, а не от всей таблицы.
- Для артефакта сначала загрузить `RawDocument`, определить его run/source/project и проверить доступ.
- Добавить интеграционные тесты с двумя пользователями и двумя проектами на read, write, download и review.

### P0-2. SSRF через источники, профилировщик, selector picker и workflow-узлы

**Где:** [sources.py](../../apps/api/app/routers/sources.py), [source_profiler.py](../../apps/api/app/services/source_profiler.py), [selector_picker.py](../../apps/api/app/services/selector_picker.py), [nodes.py](../../packages/workflow_engine/nodes.py).

**Ошибка.** URL, полученный от пользователя, передаётся в `httpx` и Playwright без единой проверки схемы, DNS-адреса или принадлежности домену. Это относится к `/sources/profile`, `/sources/selector-snapshot`, `http_request`, `download_file`, `browser_open`, `pagination`, `crawl_links`, `follow_links` и `send_webhook`. В HTTP-клиентах включены redirects, но конечный URL после redirect не валидируется.

**Влияние.** Роли Developer и Operator могут заставить API/worker обращаться к loopback, внутренним контейнерам и облачным metadata endpoints, сканировать сеть и считывать ответ в NodeRun, артефакт, profiler/snapshot или отправлять данные в произвольный webhook. В браузерном worker риск шире: запрос выполняется в окружении с доступом к внутренней сети и, возможно, с browser storage state.

**Как улучшить.**

- Создать единый сетевой policy layer для всех HTTP/Playwright вызовов.
- Разрешать только `http`/`https`; запрещать userinfo, `file:`, `data:`, нестандартные схемы и неоднозначные IP-формы.
- Резолвить hostname и отклонять loopback, link-local, RFC1918, carrier-grade NAT, multicast, IPv6 ULA/link-local и cloud metadata IP; выполнять проверку повторно на каждом redirect, чтобы закрыть DNS rebinding.
- В production применить outbound proxy/egress firewall и allowlist доменов на уровне проекта; не считать UI-переключатель `same_origin_only` достаточной границей безопасности.
- Ограничить доступ к диагностическим URL endpoints администраторами либо отдельной capability.

### P0-3. `node-test` раскрывает секреты, пароли внешних БД и ключи AI-провайдеров

**Где:** [workflows.py](../../apps/api/app/routers/workflows.py), `test_node` и `build_execution_variables`; [nodes.py](../../packages/workflow_engine/nodes.py), `render_template` и `SetConstantNode`.

**Ошибка.** `POST /workflows/node-test` доступен Administrator, Developer и Operator. Он вызывает `build_execution_variables`, который загружает в контекст все записи `Secret`, все активные `DatabaseConnection` с URL, включающими пароль, и все активные AI provider API keys. Конфигурация тестируемого узла контролируется клиентом. Шаблонизатор поддерживает `{{secret.NAME}}`, а значение переменных можно вернуть через `set_constant`/mapping и получить в HTTP-ответе node-test.

**Влияние.** Operator может извлечь `APP_SECRET_KEY` (через `_CRAWL_RESUME_SECRET`), произвольные application secrets, пароли внешних БД и API keys. Это прямое повышение привилегий и компрометация всех подключений.

**Как улучшить.**

- Не передавать секреты и URL с паролями в node-test вообще.
- Для теста ввести отдельный capability-scoped контекст: только явно выбранное подключение/провайдер, только после проверки project access и только с серверным разрешением операции.
- Убрать API keys из `variables`; хранить их только в недоступном шаблонизатору secret provider.
- Redact/denylist output, NodeRun preview и artifacts для секретных полей.
- До переработки ограничить endpoint ролью Administrator и отключить template expansion `secret.*` в preview.

### P0-4. Глобальные подключения, browser profiles и AI providers доступны workflow любого Developer

**Где:** [settings.py](../../apps/api/app/routers/settings.py), [workflows.py](../../apps/api/app/routers/workflows.py), [nodes.py](../../packages/workflow_engine/nodes.py).

**Ошибка.** `DatabaseConnection`, `BrowserProfile`, `AIProviderConfig` и `Secret` глобальны: у них нет `project_id`/owner scope. `build_execution_variables` передаёт в каждый run все enabled connections и providers. Любой Developer видит имена/конфигурацию и может собрать workflow с `save_external_db`, LLM или source settings, указывающими на чужой browser profile.

**Влияние.** Developer одного проекта может записывать в разрешённые таблицы внешних БД другого проекта, расходовать чужие AI credentials, использовать browser storage state/cookies другого профиля и получать доступ к данным за пределами своей области ответственности.

**Как улучшить.**

- Добавить owner project/organization и ACL к каждому integration object.
- В workflow разрешать только явные connection/provider/profile bindings, проверенные для данного проекта и версии workflow.
- `allowed_tables` сделать обязательным non-empty allowlist; по умолчанию запрещать запись.
- Не передавать credentials в обычных variables и не давать Developer свободно выбирать глобальный browser profile.

### P1-1. Нарушена связность объектов разных проектов

**Где:** [schemas.py](../../apps/api/app/routers/schemas.py), [sources.py](../../apps/api/app/routers/sources.py), [data.py](../../apps/api/app/routers/data.py), [workflows.py](../../apps/api/app/routers/workflows.py), [settings.py](../../apps/api/app/routers/settings.py).

**Ошибка.** При создании Source/Schema/Workflow/Schedule не подтверждается существование и доступность указанного проекта/родительского объекта. `DatasetCreate` и `DatasetUpdate` проверяют существование schema, но не её `project_id`; `run_workflow` проверяет лишь существование `source_id`, но не принадлежность source проекту workflow; `persist_result` принимает dataset из graph без сравнения с `workflow.project_id`.

**Влияние.** Можно связать workflow проекта A с source, schema или dataset проекта B. Даже в single-tenant это смешивает данные, review policy и provenance; вместе с P0-1 становится способом менять или извлекать чужие данные.

**Как улучшить.**

- Во всех create/update/run путях проверять existence + `project_id` у каждой ссылки.
- На уровне БД использовать composite foreign keys или отдельные binding tables, когда это возможно; при JSON graph — валидатор связей при publish/run.
- Добавить инвариант: source, workflow, dataset, schema и schedule одного pipeline обязаны принадлежать одному проекту.

### P1-2. Отмена run не отменяет выполняющийся worker и может быть перезаписана успехом

**Где:** [runs.py](../../apps/api/app/routers/runs.py), [workflows.py](../../apps/api/app/routers/workflows.py), [engine.py](../../packages/workflow_engine/engine.py).

**Ошибка.** Endpoint cancel только записывает `Run.status = CANCELLED`. Worker перед запуском проверяет отмену один раз, затем не перечитывает БД и `ExecutionContext.cancelled` никогда не меняется. По завершении он безусловно вычисляет и записывает финальный status.

**Влияние.** Уже выполняющийся crawler/LLM/browser run продолжает внешние действия, сохраняет артефакты и записи, а статус `CANCELLED` может стать `SUCCESS`/`FAILED`. Пользователь получает ложное ощущение отмены; возможны лишние расходы и нежелательная запись в внешние БД/webhook.

**Как улучшить.**

- Хранить cancellation flag в Redis/БД и проверять его между узлами, страницами crawler и batch-операциями.
- Использовать Celery revoke для queued task и cooperative cancellation для running task.
- Финальное обновление делать compare-and-set только из `RUNNING`; не перезаписывать terminal `CANCELLED`.
- Для side-effect nodes добавить check перед операцией и idempotency key.

### P1-3. Версии workflow не всегда являются неизменяемыми снимками

**Где:** [workflows.py](../../apps/api/app/routers/workflows.py), `update_workflow`, `active_graph`, `run_workflow`.

**Ошибка.** `Workflow.version` увеличивается при PATCH, но `WorkflowVersion` создаётся только при publish. При запуске draft версии `active_graph` не находит snapshot и возвращает текущий `workflow.graph_json`.

**Влияние.** Run хранит номер версии, но не обязательно именно граф, который был выполнен. После последующего редактирования невозможно корректно воспроизвести execution, расследовать инцидент или гарантировать, что queued run использует ожидаемую конфигурацию.

**Как улучшить.**

- В транзакции каждого изменения graph создавать immutable `WorkflowVersion`.
- Создавать Run только со ссылкой на существующий snapshot (лучше `workflow_version_id`, не число).
- Плановые и ручные runs использовать одинаковый механизм snapshot; draft запускать только как explicit preview с сохранённым графом.

### P1-4. Планировщик допускает дублирование запусков при нескольких beat-экземплярах

**Где:** [worker.py](../../apps/worker/worker.py), `schedule_tick`.

**Ошибка.** Проверка `last_run_at` выполняется в памяти одной сессии и не блокирует строку schedule. Два Celery Beat, запущенные параллельно, могут оба определить schedule как due и поставить одинаковые runs в очередь.

**Влияние.** Дублированный crawling/LLM, двойная запись/экспорт/webhook, дополнительная нагрузка и затраты.

**Как улучшить.**

- Использовать distributed lock (Redis) или PostgreSQL advisory lock на schedule+minute.
- Либо создать таблицу schedule occurrences с unique `(schedule_id, scheduled_at_minute)` и вставлять её до enqueue.
- Обернуть claim schedule в короткую транзакцию `SELECT ... FOR UPDATE SKIP LOCKED`.

### P1-5. Production может запуститься с известными default secret и admin password

**Где:** [config.py](../../apps/api/app/config.py), [bootstrap.py](../../apps/api/app/bootstrap.py).

**Ошибка.** `APP_SECRET_KEY`, `ENCRYPTION_MASTER_KEY`, S3 secret и пароль начального администратора имеют рабочие defaults. При отсутствии environment variables bootstrap создаёт предсказуемую admin-учётную запись, а JWT подписываются опубликованным ключом.

**Влияние.** Компрометация JWT, доступ к первичному Administrator, расшифровка секретов при ошибочной конфигурации production.

**Как улучшить.**

- При `APP_ENV=production` требовать все критичные переменные и завершать процесс до миграций/bootstrap.
- Запрещать известные/короткие значения и хранить dev defaults только в test fixture, не в runtime settings.
- Первый administrator создавать через одноразовый bootstrap token или явно переданный сильный пароль.

### P1-6. Неограниченные ответы и network capture позволяют исчерпать память/диск

**Где:** [nodes.py](../../packages/workflow_engine/nodes.py), `HTTPRequestNode`, `DownloadFileNode`, `BrowserOpenNode`, `CrawlLinksNode`, `response_payload`, `store_artifact`.

**Ошибка.** HTTP responses читаются полностью в память; browser сохраняет full-page screenshot, rendered DOM и JSON/XHR bodies; crawler может сохранить артефакты для многих страниц. Нет лимита размера ответа, суммарного объёма artifacts/network capture или page output.

**Влияние.** Внешний сайт либо намеренно созданный URL может вызвать memory/disk exhaustion worker/API, большой счёт за S3 и недоступность сервиса. Сохранённые JSON/XHR responses могут также содержать чувствительные данные.

**Как улучшить.**

- Ввести per-response `Content-Length` precheck и streaming hard cap.
- Ограничить число и размер network entries, HTML, screenshot, artifacts и общий byte budget на run.
- По умолчанию отключить full-page screenshot и capture response bodies; сохранять metadata/хеш, body — только по явному флагу.
- Установить quotas per project/user и retention/cleanup policy.

### P1-7. Разбор документов уязвим к resource exhaustion

**Где:** [documents.py](../../apps/api/app/routers/documents.py), [nodes.py](../../packages/workflow_engine/nodes.py), `ParseDocumentNode`.

**Ошибка.** API ограничивает исходный upload 100 MB, но загружает целый XLSX/PDF/CSV/DOCX в память и материализует все строки/страницы. Нет ограничений на распакованный ZIP размер, число sheets/rows/cells/pages, CPU-время OCR/Docling и memory subprocess.

**Влияние.** Аутентифицированный пользователь может вызвать CPU/RAM exhaustion ZIP bomb, huge CSV/XLSX/PDF или тяжёлым документом. В API это блокирует web process, в worker — очередь.

**Как улучшить.**

- Обрабатывать документы только в изолированном worker/process/container с cgroup memory/CPU/time limits.
- Ограничить decompressed size, pages, rows, cells, sheets, text length и время OCR.
- Для CSV/XLSX применять streaming/iterative parsing вместо построения полного списка.
- Валидировать MIME по содержимому, а не только по расширению.

### P1-8. Синхронные workflow runs могут исчерпать API workers

**Где:** [schemas.py](../../apps/api/app/schemas.py), `RunRequest.synchronous=True`; [workflows.py](../../apps/api/app/routers/workflows.py), `run_workflow`.

**Ошибка.** Ручной запуск синхронный по умолчанию и выполняет crawler, Playwright, document parsing или LLM прямо в request lifecycle API. Есть большие per-node timeouts (например, 900 сек. для crawler) и нет rate limit/concurrency quota по пользователю.

**Влияние.** Несколько пользователей могут занять все ASGI workers медленными URL/LLM, из-за чего login, UI и health endpoints становятся недоступными.

**Как улучшить.**

- Сделать async queue единственным production-путём; synchronous разрешить только коротким, безопасным preview-запускам.
- Ввести per-project/user concurrency и queue quotas.
- Возвращать `202 Accepted` + run ID, следить за результатом через SSE/polling.

### P1-9. Небезопасная модель записи во внешние БД

**Где:** [settings.py](../../apps/api/app/routers/settings.py), [workflows.py](../../apps/api/app/routers/workflows.py), [nodes.py](../../packages/workflow_engine/nodes.py), `SaveExternalDatabaseNode`.

**Ошибка.** Все enabled external connections передаются в run каждого workflow. Пустой `allowed_tables` означает разрешение на все таблицы; Developer может вызвать `save_external_db`. Поле `connection_options.url` полностью подменяет сформированный URL, а `ssl_mode` сохраняется, но не применяется в `connection_url`.

**Влияние.** Ошибка разграничения даёт возможность записи во внешнюю БД за пределами проекта; некорректный/неприменённый TLS policy может привести к подключению без ожидаемой защиты. Администратор может также случайно сохранить произвольный URL, который затем используется без отдельной валидации.

**Как улучшить.**

- Ввести проектный binding connection-to-workflow и отдельное разрешение `external_db:write`.
- Сделать allowlist таблиц обязательным; проверять schema и conflict keys до выполнения.
- Не принимать произвольный `connection_options.url` либо валидировать и хранить структурированные параметры.
- Передавать SSL mode в конкретный dialect/driver connect args и добавить тесты TLS-конфигурации.

### P1-10. Browser profile со storage state можно привязать к произвольному Source

**Где:** [schemas.py](../../apps/api/app/schemas.py), `SourceCreate.settings`; [settings.py](../../apps/api/app/routers/settings.py); [workflows.py](../../apps/api/app/routers/workflows.py), `build_execution_variables`.

**Ошибка.** Source settings — свободный JSON. Developer может создать source с `browser_profile_id`; список browser profiles доступен Developer, а профиль глобальный. При запуске его decrypted storage state передаётся в browser context без project/owner проверки.

**Влияние.** Browser session/cookies одного проекта могут использоваться для запросов другого проекта или произвольных URL. В сочетании с SSRF это повышает риск доступа к приватным системам.

**Как улучшить.**

- Сделать browser profiles project-scoped и проверять ownership при source create/update/run.
- Не позволять задавать security-sensitive IDs в свободном `settings`; использовать отдельную schema и endpoint binding.
- Ограничить allowed origins для profile/cookies и раздельно хранить session state для конкретного source/domain.

### P2-1. Нет rate limit и защиты от brute force для login и большинства API endpoint’ов

**Где:** [auth.py](../../apps/api/app/routers/auth.py), [main.py](../../apps/api/app/main.py), [rate_limit.py](../../apps/api/app/services/rate_limit.py).

**Ошибка.** Rate limit реализован только для machine API token в Data API. `/auth/login`, `/auth/refresh`, profiler, node-test, document parsing, запуска workflow и прочие дорогие endpoint’ы не имеют ограничения частоты, IP/user quotas либо account lockout.

**Влияние.** Возможен перебор паролей, расход ресурсов API/worker и abuse публичных функций при наличии одной учётной записи.

**Как улучшить.**

- Добавить reverse-proxy и application rate limits: IP + account для login, user/project + cost-weighted limits для тяжёлых действий.
- Ввести exponential backoff/temporary lockout и audit events для auth failures.
- Ограничить размер и частоту request bodies ещё до запуска обработчика.

### P2-2. Refresh token нельзя выборочно отозвать или обнаружить повторное использование

**Где:** [security.py](../../apps/api/app/security.py), [auth.py](../../apps/api/app/routers/auth.py).

**Ошибка.** Refresh JWT самодостаточен: в нём нет `jti`, сервер не хранит сессии/rotation chain и не может отозвать отдельный токен. Проверяется только активность пользователя.

**Влияние.** Украденный refresh token действует до истечения срока. Невозможны logout-all/one-session, аудит сессий и реакция на повторное использование токена.

**Как улучшить.**

- Добавить session table с `jti`, expiry, revocation и device metadata.
- Использовать refresh rotation: при каждом refresh выдавать новый токен и отзывать старый; повторное применение старого токена отзывает всю цепочку.
- Добавить endpoint logout/revoke и короткий TTL access token.

### P2-3. Ошибки Celery enqueue/retry обрабатываются недостоверно

**Где:** [worker.py](../../apps/worker/worker.py), [workflows.py](../../apps/api/app/routers/workflows.py).

**Ошибка.** `execute_run_task` настроен с `autoretry_for=(Exception,)`, но `execute_run` перехватывает любые исключения, записывает FAILED и нормально возвращается; Celery retry не запускается. При отказе Redis `enqueue_run` вызывается после commit, поэтому Run может остаться `QUEUED`, хотя задача не попала в broker.

**Влияние.** Ожидаемые transient retries не происходят, а пользователь может видеть вечный queued run без причины. Это снижает надёжность внешних интеграций и усложняет восстановление.

**Как улучшить.**

- Классифицировать retryable ошибки и либо пробрасывать их до Celery, либо вызывать `self.retry` явно.
- Ввести transactional outbox: в одной транзакции создать Run и событие enqueue; отдельный dispatcher надёжно доставляет его в broker.
- Помечать enqueue failure/status reason и добавлять watchdog для зависших QUEUED/RUNNING runs.

### P2-4. `Base.metadata.create_all()` в runtime конфликтует с Alembic

**Где:** [main.py](../../apps/api/app/main.py), [migrations/0001_initial.py](../../migrations/versions/0001_initial.py).

**Ошибка.** API при каждом старте вызывает `Base.metadata.create_all`, несмотря на migration lifecycle через Alembic. Первая миграция также вызывает `create_all`.

**Влияние.** Возможны schema drift, гонки при запуске нескольких API instances и обход дисциплины reviewable migrations. Ошибка особенно опасна при разных версиях приложения и БД.

**Как улучшить.**

- Удалить `create_all` из application lifespan.
- Миграции писать явными Alembic operations; bootstrap выполнять только после успешного `alembic upgrade head`.
- В CI тестировать upgrade с пустой и с предыдущей схемой.

### P2-5. S3 error незаметно меняет хранилище на local fallback

**Где:** [artifact_storage.py](../../apps/api/app/services/artifact_storage.py).

**Ошибка.** Любое исключение при `put_s3` превращается в локальную запись с warning вместо ошибки workflow.

**Влияние.** Ошибка прав, сети или неверного bucket маскируется. Артефакты могут остаться на ephemeral filesystem API/worker, исчезнуть при рестарте и не соответствовать заявленной retention/policy.

**Как улучшить.**

- По умолчанию fail closed: storage error должен завершать узел/run с диагностикой.
- Local fallback разрешать только отдельным explicit development setting; отправлять метрику/alert.
- Сохранять backend origin однозначно и проверять доступность artifact после записи.

### P2-6. Неаутентифицированный `/metrics` раскрывает operational data

**Где:** [system.py](../../apps/api/app/routers/system.py).

**Ошибка.** Endpoint доступен без auth и возвращает количество runs, failures, sources, review tasks и LLM calls.

**Влияние.** Внешний наблюдатель получает информацию об активности, ошибках и масштабе системы; endpoint также может использоваться для лишней нагрузки.

**Как улучшить.**

- Закрыть endpoint ingress network policy/Prometheus service account или вынести на отдельный private port.
- При необходимости публичного health оставить только минимальный `/health`, не раскрывающий состояние.

### P2-7. Аудит-лог не содержит заявленных request metadata и неполон для sensitive actions

**Где:** [models.py](../../apps/api/app/models.py), `AuditLog`; [audit.py](../../apps/api/app/audit.py).

**Ошибка.** Модель имеет `ip` и `user_agent`, но helper `audit()` их никогда не заполняет. Ряд sensitive endpoints (например create/update schema, prompt, browser profile, запуск/отмена workflow) не создаёт audit event.

**Влияние.** Невозможно надёжно расследовать действие, источник запроса и изменение security-sensitive конфигурации; разрушается ожидаемая ценность audit trail.

**Как улучшить.**

- Передавать Request context (trusted client IP через правильно настроенный proxy, user agent, request ID) в audit helper.
- Сформировать обязательную матрицу audit events для CRUD, secrets/integrations, workflow execution, review и token lifecycle.
- Исключать секретные значения, но хранить masked diff, actor, target project и result.

### P2-8. Конфигурация CORS и production environment не проходит строгую валидацию

**Где:** [config.py](../../apps/api/app/config.py), [main.py](../../apps/api/app/main.py), Compose files.

**Ошибка.** Origins принимаются строкой без production validation; сервис допускает insecure defaults. CORS middleware разрешает все methods/headers и credentials, полагаясь только на конфигурацию окружения.

**Влияние.** Ошибка deploy-конфигурации может открыть credentialed browser access неподходящим origins. Хотя Bearer token обычно не отправляется автоматически, риск повышается при будущем добавлении cookies/API changes.

**Как улучшить.**

- Валидировать origins как exact HTTPS origins в production, запретить `*` при credentials.
- Ограничить методы/headers реальным API contract.
- Проверять конфигурацию отдельным startup check и deployment test.

### P2-9. Неприкреплённые raw artifacts могут попасть в чужую provenance цепочку в рамках run

**Где:** [workflows.py](../../apps/api/app/routers/workflows.py), `raw_document_for_item`, `add_record_observation`.

**Ошибка.** Если item содержит `raw_artifact.sha256`, поиск выбирает последний `RawDocument` того же run с таким hash. Несколько URL могут иметь одинаковое содержимое/hash; связь не требует совпадения URL/kind или явного immutable artifact ID.

**Влияние.** Неверная доказательная цепочка: RecordObservation может ссылаться на документ с другого URL той же run. Это не даёт произвольного чтения, но ухудшает расследуемость, экспорт provenance и доверие к данным.

**Как улучшить.**

- Передавать internal immutable artifact ID от `store_artifact` до persistence, а не искать по hash.
- Проверять run, URL, kind и content hash; не делать эвристический fallback при неоднозначности.
- Добавить unique artifact identity и тест на одинаковые bodies разных URL.

### P2-10. Нет автоматической очистки artifacts, usage buckets, node runs и временных данных

**Где:** [models.py](../../apps/api/app/models.py), [artifact_storage.py](../../apps/api/app/services/artifact_storage.py), [worker.py](../../apps/worker/worker.py).

**Ошибка.** Видны модели и запись объёмных artifacts/NodeRun/usage buckets, но нет retention job, TTL или quota cleanup.

**Влияние.** База, Redis/S3/local storage будут неограниченно расти; повышается стоимость, ухудшается производительность и срок хранения потенциально чувствительных данных становится неуправляемым.

**Как улучшить.**

- Определить retention policy по типу artifact/run/audit и legal hold.
- Реализовать scheduled cleanup с batch delete, безопасной проверкой ссылок и метриками.
- Ввести S3 lifecycle rules и индексы для удаления/архивации.

### P3-1. Dependencies и container images не зафиксированы

**Где:** [requirements.txt](../../requirements.txt), [pyproject.toml](../../pyproject.toml), [docker-compose.yml](../../docker-compose.yml).

**Ошибка.** Python dependencies заданы только нижними границами, а MinIO использует `latest`.

**Влияние.** Сборки нерепродуцируемы; будущий release зависимости может сломать приложение или принести уязвимость/несовместимость.

**Как улучшить.**

- Использовать lockfile с точными версиями и hashes (`uv.lock`, pip-tools или Poetry), регулярно обновлять по security policy.
- Фиксировать Docker image tag/digest, включая MinIO.
- Добавить dependency scanning/SBOM в CI.

### P3-2. В `connection_url` не применяется сохранённый `ssl_mode`

**Где:** [settings.py](../../apps/api/app/routers/settings.py).

**Ошибка.** Поле `ssl_mode` сохраняется, но при формировании URL/connect args не используется.

**Влияние.** Администратор может считать подключение защищённым TLS-политикой, хотя драйвер использует собственный default. Это ведёт к ошибочной конфигурации защиты канала.

**Как улучшить.**

- Явно маппировать SSL options на параметры каждого поддерживаемого dialect/driver.
- При создании и test connection показывать effective SSL configuration и проверять сертификат при требуемом режиме.

### P3-3. Избыточно широкие exception handlers скрывают классы ошибок

**Где:** [workflows.py](../../apps/api/app/routers/workflows.py), [nodes.py](../../packages/workflow_engine/nodes.py), [source_profiler.py](../../apps/api/app/services/source_profiler.py), [worker.py](../../apps/worker/worker.py).

**Ошибка.** Во многих critical paths используется `except Exception`, иногда с fallback или silent continue.

**Влияние.** Ошибки конфигурации, programming errors и временные сетевые ошибки получают одинаковое поведение; усложняются retry policy, alerting и диагностика. В crawler/browser часть проблем может скрываться как partial result.

**Как улучшить.**

- Ловить конкретные исключения (`httpx`, Playwright, storage, validation) и централизованно классифицировать retryable/non-retryable.
- Логировать exception type, stack trace и safe context; не превращать programming errors в normal workflow data.
- Определить единый контракт partial failure и minimum-success policy.

## Устаревшие или неиспользуемые части

### U-1. Неиспользуемый параметр `include_legacy`

**Где:** [workflows.py](../../apps/api/app/routers/workflows.py), `list_workflows`.

**Наблюдение.** Параметр endpoint объявлен, но не участвует в формировании SQL-запроса или ответа.

**Влияние.** API обещает поведение, которого нет; клиенты могут строить неверную логику фильтрации.

**Предложение.** Либо реализовать документированную фильтрацию через явный metadata/version field, либо удалить параметр и обновить OpenAPI/UI.

### U-2. Три неиспользуемых helper-функции в Data API

**Где:** [data.py](../../apps/api/app/routers/data.py): `observation_matches_time`, `sort_observation_rows`, `observation_key_is_after`.

**Наблюдение.** Поиск по репозиторию показывает, что функции определены, но не вызываются. Фактическая пагинация выполняется SQL-функциями из `services/data_records.py`.

**Влияние.** Дублируются две модели сортировки/фильтрации, будущий разработчик может исправить неиспользуемую ветку и считать дефект закрытым.

**Предложение.** Удалить функции после проверки coverage либо вынести в unit-tested общий query helper и реально использовать.

### U-3. `hydrate_dynamic_detail` — compatibility no-op

**Где:** [nodes.py](../../packages/workflow_engine/nodes.py).

**Наблюдение.** Функция явно возвращает входной HTTP response без обработки и не имеет вызовов в текущем коде.

**Влияние.** Создаёт ложное впечатление, что dynamic detail hydration поддерживается отдельным механизмом; поддержка legacy imports без deprecation срока увеличивает технический долг.

**Предложение.** Удалить после переходного периода либо перенести в dedicated compatibility module с deprecation notice и тестом обратной совместимости.

### U-4. Legacy-настройки crawler остаются в публичном catalog при очистке templates

**Где:** [catalog.py](../../packages/workflow_engine/catalog.py), [workflow_templates.py](../../apps/api/app/routers/workflow_templates.py).

**Наблюдение.** Catalog всё ещё публикует `title_selector`, `date_selector`, `body_selector`, `tag_selector`, `attachment_selector`, `source_name` и прочие legacy поля. При сохранении portable template `_clean_graph` их удаляет.

**Влияние.** UI может позволить пользователю настроить поля, которые затем исчезают при сохранении шаблона; это непредсказуемое поведение и поддержка двух API-контрактов.

**Предложение.** Либо окончательно мигрировать legacy config в `detail_fields`, либо сохранить его формально как versioned legacy schema и показывать только для migration mode.

### U-5. Исторический forensic audit содержит устаревшие факты

**Где:** [FORENSIC_AUDIT.md](FORENSIC_AUDIT.md).

**Наблюдение.** Документ указывает, например, что `canonical_url()` удаляет все query params и что SSE не завершает empty success. В текущем коде `canonical_url` сохраняет identity query parameters, а `TERMINAL_RUN_STATUSES` включает оба empty success status.

**Влияние.** Смешение исторических и актуальных находок может направить ремонт в неверную сторону.

**Предложение.** Пометить документ как historical snapshot с commit hash/датой и добавить ссылку на данный актуальный аудит; не использовать старые пункты как текущий backlog без повторной проверки.

## Что проверено и ограничения проверки

- Проверены все Python-файлы бэкенда, workflow engine, миграции, Compose/Docker конфигурация, основные интеграционные и unit tests как спецификация поведения.
- Выполнена синтаксическая проверка: `python -m compileall` для `apps/api`, `apps/worker`, `packages` и `migrations` — успешно.
- Рабочее дерево до и после аудита чистое; приложение не изменялось.
- Полный `pytest` и `ruff` в предоставленной оболочке не были запущены: системный Python 3.14 не содержит `fastapi` и `ruff`; bundled workspace Python также не содержит package environment проекта. Это ограничение окружения аудита, а не доказательство ошибки приложения.
- Не выполнялись активные запросы к внешним сайтам и не проводилась эксплуатация SSRF/утечки секретов на работающем сервисе: выводы основаны на достижимости путей в коде и должны быть подтверждены безопасными локальными regression tests при исправлении.

## Рекомендуемый порядок исправлений

1. До любого публичного/multi-user deploy: P0-1, P0-2, P0-3, P0-4 и production secret validation.
2. Затем: project-reference invariants, cancellation semantics, immutable workflow snapshots, scheduler locking и resource budgets.
3. После этого: async-only execution, rate limiting, external DB policy, storage reliability и audit retention.
4. В отдельном maintenance PR: убрать U-1..U-4, актуализировать историческую документацию и зафиксировать dependencies.

