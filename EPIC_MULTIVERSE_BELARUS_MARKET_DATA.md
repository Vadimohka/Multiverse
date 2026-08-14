# EPIC: Belarus Market Data Pack v1 для Multiverse

**Repository:** https://github.com/Vadimohka/Multiverse
**Target branch baseline:** актуальный `main` на момент выполнения
**Research snapshot used for this EPIC:** 13.08.2026
**Business passports:**

- `PASSPORT_UL_DEPOSITS.md`
- `PASSPORT_FL_DEPOSITS.md`
- `PASSPORT_MARKET_NEWS.md`

---

# 1. Mission для coding-агента

Доработай Multiverse и создай готовый production-oriented пакет универсальных workflows, который по API стабильно выдаёт:

```text
deposit-offers-legal   — банковские депозиты/размещение средств ЮЛ
deposit-offers-retail  — вклады/депозиты ФЛ
market-news            — нормализованный поток публикаций для внешнего ИИ
market-indicators      — отдельный рекомендуемый dataset для НБРБ/БВФБ rates/REPO и иных рыночных индикаторов
```

Внешний ИИ-агент является отдельной системой. Он получает данные через Data API и не должен знать DOM, CSS selectors, XHR endpoints, PDF structure или browser actions.

Конечный результат — **не документация о том, как можно сделать**, а код + schemas + presets + fixtures + instantiated workflows + API contract + tests.

---

# 2. Непереговорные архитектурные принципы

Multiverse — универсальная no-code workflow platform, а не коллекция site-specific parsers.

## Запрещено

```text
ParseBelarusbankNode
PrimePressParserNode
TCBankTableParser
if hostname == "..."
if bank == "..."
hardcoded CSS/JSONPath конкретного домена внутри generic strategy
скрытый HTTP→browser fallback
```

## Допустимо

Новая capability/операция только если она:

1. имеет независимый вход/выход;
2. применима к нескольким независимым источникам;
3. не выражается чисто существующей конфигурацией;
4. покрывается generic fixtures.

Вся site-specific логика должна находиться в `SourcePresetRevision`, schema/mapping configuration, fixtures и workflow revision.

---

# 3. Сначала изучи текущий main — фундамент уже существует

Перед кодированием обязательно изучи:

- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/packages/workflow_engine/contracts.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/packages/workflow_engine/strategies.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/packages/workflow_engine/nodes.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/apps/api/app/routers/presets.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/apps/api/app/routers/data.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/apps/api/app/seed_templates.py
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/docs/audit/UNIVERSAL_SCRAPER_BLUEPRINT_2026-08-12.md
- https://raw.githubusercontent.com/Vadimohka/Multiverse/main/docs/audit/DATA_API_CONTRACT.md

## Уже подтверждено в текущем main

### Contract v2 уже есть

`packages/workflow_engine/contracts.py` содержит:

```text
CONTRACT_VERSION = 2
ADAPTIVE_MODES = AUTO / ASSISTED / MANUAL
```

Публичный facade уже закрепляет семь фаз:

```text
manual_trigger → Start
http_request   → Acquire
crawl_links    → Traverse
mapping        → Extract
transform      → Process
validate       → Assure
output         → Output
```

Typed envelopes уже существуют:

```text
RunContext@2
SourceBundle@2
RecordSet@2
RunAssessment@2
OutputReceipt@2
```

`AdaptiveAttempt` уже хранит strategy, timing, selection, postconditions, fallback reason, artifacts, error, request ref, budget counters.

`standard_v2_graph()` уже создаёт seven-phase skeleton.

**Не переписывай это.**

### SourcePreset compiler уже есть

`apps/api/app/routers/presets.py` уже умеет create/compile/instantiate source preset.

Также уже enforced:

```text
SourcePreset status=VERIFIED requires at least one fixture ref
```

Не создавай параллельную систему presets.

### Assure уже не пустой

Текущий `ValidateNode` уже поддерживает, в частности:

```text
expectedScope.minRecords
expectedScope.requireComplete
expectedScope.allowEmpty
reconciliation
PASS / PARTIAL / FAIL
EMPTY_UNEXPECTED
```

Расширяй его, а не заменяй.

### Traverse уже умеет основные виды pagination

Текущая strategy поддерживает:

```text
page
offset
cursor
next URL / selector
maxPages
REPEATED_PAGE
NO_NEXT
PAGE_FAILED
MAX_PAGES
list→detail
```

Не создавай второй traversal engine.

---

# 4. Baseline / Story BMD-001

Перед первым функциональным PR:

1. checkout latest `main`;
2. установить зависимости;
3. запустить существующие unit/integration/backend tests;
4. получить список текущих node/strategy capabilities;
5. проверить миграции DB;
6. проверить существующие fixtures;
7. сохранить короткий `docs/belarus_market/BASELINE.md` с:
   - commit SHA;
   - test status;
   - already implemented requirements;
   - real gaps;
   - discovered conflicts with this EPIC.

Если требование EPIC уже качественно реализовано — **не дублируй его**. Добавь regression test и используй существующее.

---

# 5. Story BMD-002 — generic collection transforms в Process

## Проблема

Текущий `apply_operation(row, operation)` работает преимущественно как `row → row` и поддерживает операции вроде:

```text
rename
constant
trim
normalize_spaces
replace
regex
number
integer
currency
term
rate
map
split
concat
```

В текущем `nodes.py` не найдено `explode` или `unpivot`.

Банковские источники регулярно возвращают матрицы:

```text
product | min amount | 32–61 | 62–91 | 92–185 | 186–366
A       | 50 000     | 6.0   | 7.0   | 8.0    | 9.5
```

Из неё надо получить четыре independent business records без банковского кода.

## Requirement

Добавь generic `records → records` stage внутри существующего Process facade/strategy.

Не добавляй новый публичный node type.

Минимум операций:

### `explode`

Array field → N records.

### `matrix_to_records` / `unpivot`

Config-driven conversion matrix columns → dimension/value records.

Пример декларации:

```yaml
- type: matrix_to_records
  idFields:
    - product_name
    - currency
    - min_amount
  dimensionColumns:
    selector: "term:*"
    headerTarget: term_raw
    valueTarget: rate_raw
  skipEmpty: true
```

### `expand_tiers`

Generic expansion structured term/amount/rate tiers.

### `select_effective_revision`

Если это архитектурно лучше реализовать отдельной generic collection operation — реализуй здесь; если лучше как reusable resolver service, Process вызывает его декларативно.

## Evidence requirement

После unpivot evidence значения должно продолжать указывать на:

```text
source row
source column/header
source cell
raw artifact
```

## Generic fixtures

Минимум:

- HTML bank-like matrix без банковского имени;
- amount tiers;
- empty cells;
- merged/repeated headers if supported;
- stable evidence after expansion.

---

# 6. Story BMD-003 — date-bounded Traverse

## Проблема

News jobs обрабатывают точный интервал, но текущий Traverse знает pagination и stop reasons, а declarative `dateBoundary` в `strategies.py` отсутствует.

Без этого parser ежедневно либо обходит слишком много страниц, либо site preset вынужден писать fragile stop logic.

## Requirement

Добавь в существующий Traverse universal date boundary.

Пример config:

```yaml
dateBoundary:
  enabled: true
  field: source_published_at
  lowerBound: "{{run.from}}"
  upperBound: "{{run.to}}"
  order: DESC
  stopWhenOlder: true
  timezone: Europe/Minsk
```

### Semantics

- interval строго `[from, to)`;
- timestamp должен быть timezone-aware после normalization;
- lower-bound early stop только если preset явно утверждает порядок `DESC`/`ASC` и fixture это проверяет;
- если ordering не доказан — continue traversal до обычных stop conditions и фильтруй records;
- listing date может извлекаться declarative field candidate до detail fan-out;
- details внутри окна обрабатываются полностью;
- older cards могут не fan-out'иться после доказанного boundary.

Добавить stop reason:

```text
DATE_BOUNDARY_REACHED
```

Сохранить:

```text
boundary value
last seen source timestamp
page URL
ordering assumption
```

в traversal diagnostics/evidence.

## Tests

- descending pages;
- ascending pages;
- unordered list: no early stop;
- missing date on one card;
- timezone edge;
- Monday multi-day window;
- repeated page before/after boundary.

---

# 7. Story BMD-004 — universal current/effective revision resolver

## Проблема

Банки публикуют:

```text
текущие условия
условия "до 30.04"
условия "с 04.05"
архив
несколько PDF/DOCX
старую и новую таблицу одновременно
```

Выбор «первой ссылки PDF» недопустим.

## Requirement

Добавь generic resolver для HTML blocks и linked documents.

Пример configuration:

```yaml
effectiveRevision:
  enabled: true
  effectiveAt: "{{run.clock}}"
  scopeSelector: "..."
  candidates:
    dateSources:
      - label
      - heading
      - table_caption
      - document_filename
      - document_text
      - response_metadata
  allowMime:
    - application/pdf
    - application/vnd.openxmlformats-officedocument.wordprocessingml.document
  archiveDenyPatterns:
    - "архив"
    - "/archive/"
  preference:
    - explicit_effective_range
    - explicit_effective_from
    - newest_non_future
  ambiguityPolicy: REVIEW
```

## Output decision evidence

```json
{
  "selected_artifact": "...",
  "effective_from": "...",
  "effective_to": null,
  "candidates": [],
  "decision_rule": "explicit_effective_range",
  "ambiguity": false
}
```

## Required behavior

- run clock immutable/reproducible;
- future revision не становится current раньше срока;
- expired revision не выбирается, если есть действующая;
- ambiguity → review/partial, не guess;
- source-specific regex/labels находятся в preset, resolver code — generic.

Паритетбанк/документные банки использовать как live cases, но unit fixtures должны быть domain-neutral.

---

# 8. Story BMD-005 — расширение Assure

Сохрани существующие `minRecords`, `requireComplete`, `allowEmpty`, reconciliation.

Добавь универсальные assertions, если baseline подтвердит их отсутствие.

## `requiredFieldCoverage`

```yaml
requiredFieldCoverage:
  product_name: 1.0
  currency: 1.0
  term_min_days: 0.90
```

## `expectedStates`

Проверяет, что declarative tabs/currencies/filter states действительно были посещены/извлечены.

```yaml
expectedStates:
  mode: ALL
  states: [BYN, USD, EUR]
```

## `sourceRole`

Нужен из-за реальных retail/corporate route collisions (BelVEB, Neo и др.).

```yaml
sourceRole:
  expected: LEGAL_ENTITY
```

## `detailSuccessRatio`

```yaml
detailSuccessRatio:
  min: 0.95
```

## `documentParseRatio`

```yaml
documentParseRatio:
  min: 0.95
```

## `dateWindow`

Для news:

```yaml
dateWindow:
  from: "{{run.from}}"
  to: "{{run.to}}"
  forbidOutside: true
```

## `countDrift`

Сравнение с last comparable successful observation:

```yaml
countDrift:
  warnBelowRatio: 0.70
  warnAboveRatio: 2.0
```

Это warning/review по умолчанию, а не universal hard fail.

## Reason codes

Не смешивать состояния:

```text
EMPTY_VALID_WINDOW
EMPTY_UNEXPECTED
SOURCE_ROLE_MISMATCH
SOURCE_ACCESS_LIMITED
INCOMPLETE_TRAVERSAL
DETAIL_COVERAGE_FAILED
DOCUMENT_PARSE_INCOMPLETE
DOCUMENT_AMBIGUOUS
DATE_WINDOW_VIOLATION
COUNT_DRIFT
```

News `0 records` может быть `PASS/EMPTY_VALID_WINDOW` **только если source был реально проверен**.

Bank `0 records` по умолчанию FAIL.

---

# 9. Story BMD-006 — budget audit / deadline

`contracts.py` принимает:

```text
maxRequests
maxBytes
maxPages
maxItems
maxDepth
deadlineSeconds
```

Текущий `_Budget.from_config()` в `strategies.py` явно учитывает первые пять, но `deadlineSeconds` там не найден.

## Действие

Сначала проверь engine/runtime-level timeout.

### Если deadline уже полностью enforced выше strategies

- не дублируй;
- добавь regression test, доказывающий behavior v2 `deadlineSeconds`;
- документируй единый semantics.

### Если нет

Реализуй deadline enforcement таким образом, чтобы:

- clock был monotonic;
- каждый I/O/traversal step проверял deadline;
- попытка завершалась контролируемым budget error;
- AdaptiveAttempt содержал reason/counters;
- partial hidden success был невозможен.

---

# 10. Story BMD-007 — dataset coverage API (P0)

## Проблема

Один dataset наполняется множеством независимых source workflows.

Текущий Data API имеет:

```text
view=current
view=latest_run
view=run
view=history
```

Но `latest_run` выбирает **один** последний успешный `DatasetRun` для dataset.

Следовательно:

```text
GET ...?view=latest_run
```

не отвечает на вопрос:

> Были ли сегодня успешно проверены все 21 банк / все 16 news sources?

Внешний ИИ иначе не отличит:

```text
у банка нет данных
от
workflow банка не запускался
```

## Requirement

Добавь generic coverage endpoint.

Предпочтительный контракт:

```http
GET /api/v1/datasets/{dataset}/coverage
```

Для time-window datasets:

```http
GET /api/v1/datasets/{dataset}/coverage?from=<ISO>&to=<ISO>
```

## Example response

```json
{
  "dataset_id": "...",
  "status": "PASS",
  "expected_sources": 21,
  "checked_sources": 21,
  "successful_sources": 20,
  "partial_sources": 1,
  "failed_sources": 0,
  "window": {
    "from": null,
    "to": null
  },
  "sources": [
    {
      "source_id": "tcbank-legal",
      "workflow_id": "...",
      "run_id": "...",
      "source_preset_id": "...",
      "source_preset_revision": 3,
      "status": "PASS",
      "record_count": 24,
      "empty_reason": null,
      "assessment_codes": [],
      "finished_at": "...",
      "observed_at": "..."
    }
  ]
}
```

## Source membership

Coverage должен знать **ожидаемые** sources, а не выводить список только из тех, кто когда-либо записал record.

Используй существующую project/preset/workflow/dataset model или добавь минимальный generic dataset-source membership contract.

Не хардкодить список белорусских банков в endpoint.

## Status calculation

Generic configurable policy:

```text
PASS    — все required sources checked and acceptable
PARTIAL — часть required source assessment partial/failed, но usable records существуют
FAIL    — required coverage threshold не достигнут
```

Для news `EMPTY_VALID_WINDOW` = checked successful source с `record_count=0`.

---

# 11. Story BMD-008 — explicit field evidence API

Текущий Data API отдаёт record data + timestamps + provenance (`run_id`, `source_id`, `raw_document_id`). В кодовой базе есть internal `evidence/raw_artifact/artifacts` fields, но consumer contract не предоставляет удобный контролируемый field-level evidence interface.

## Requirement

Сначала проверь, где evidence фактически persist'ится и доступна ли она уже безопасно.

Если stable consumer contract отсутствует, реализуй один вариант:

```http
GET /api/v1/datasets/{dataset}/records?...&include=evidence
```

или

```http
GET /api/v1/datasets/{dataset}/records/{record_id}/evidence
```

## Goal

Consumer должен при необходимости доказать:

```text
rate_pct=9.5
← raw "9,5 %"
← table row 3 / column "186–366"
← artifact sha256
← URL
← run
← preset revision
```

## Constraints

- evidence не грузить в обычный compact list без запроса;
- не раскрывать secrets/cookies/authorization headers;
- сохранить backwards compatibility Data API.

---

# 12. Story BMD-009 — declarative Belarus preset pack

## Goal

Source-specific configuration должно жить рядом как reviewable versioned data, а не разрастаться в `seed_templates.py`.

Проверь существующие import/bootstrap mechanisms. Если полноценного declarative importer нет — добавь idempotent importer.

Предпочтительная структура:

```text
presets/
  belarus-market/
    README.md
    schemas/
      bank-deposit-offer-v2.json
      market-news-v1.json
      market-indicator-v1.json
    legal/
      bank-dabrabyt.yaml
      belapb.yaml
      belarusbank.yaml
      ...
    retail/
      belarusbank.yaml
      ...
    news/
      bcse-releases.yaml
      nbrb-press.yaml
      ...
    fixtures/
      ...
    policies/
      source-policy-public-belarus.yaml
```

Importer должен:

- быть idempotent;
- создавать новую immutable revision только при изменении config/content hash;
- валидировать schema/reference IDs;
- не содержать secrets;
- не переводить source в VERIFIED без fixture;
- уметь instantiate workflows декларативно.

Если repo уже имеет подходящий механизм — используй его и просто добавь pack.

---

# 13. Story BMD-010 — schemas/datasets

## Deposit schema

Не создавай отдельную несовместимую schema для каждого банка.

Используй/расширь общий `BankDepositOffer` с segment field.

Обязательные rate semantics:

```text
FIXED
VARIABLE
BENCHMARK_SPREAD
TERM_TIERED
AMOUNT_TIERED
FORMULA
INDIVIDUAL
NOT_PUBLISHED
```

ЮЛ:

```text
segment=LEGAL_ENTITY
bank/product
currency
term raw+normalized
rate raw+normalized/type
min amount
individual semantics
source/effective dates
```

ФЛ дополнительно:

```text
segment=INDIVIDUAL
revocability
replenishment_allowed
partial_withdrawal_allowed
capitalization
interest_payment_frequency
```

## Natural key

Не включать current rate.

Пример:

```text
institution_id
+ segment
+ product identity
+ variant/channel
+ currency
+ term tier
+ amount tier
```

## News schema

См. `PASSPORT_MARKET_NEWS.md`.

Natural key:

```text
source_id + stable external_id
```

fallback:

```text
source_id + canonical_url
```

## Separate indicators

НБРБ rates, BCSE FX/REPO и подобные numerical series → `market-indicators`, а не fake news/deposit records.

---

# 14. Story BMD-011 — build all ЮЛ presets + workflows

Используй `PASSPORT_UL_DEPOSITS.md` как source registry и parsing playbook.

Обязательный source set:

```text
Bank Dabrabyt
Belagroprombank
Belarusbank
Belgazprombank
Belinvestbank
VTB Belarus
BNB Bank
BelVEB
R-Bank
MTBank
Neo Bank
Paritetbank
Priorbank
RRB
Sber Bank
StatusBank
Technobank
Zepter Bank
Alfa-Bank
TC Bank
BSB Bank
```

### Important route corrections

Не повторять ошибки исходного Excel:

```text
BelVEB ЮЛ:
https://www.belveb.by/small-business/deposits/deposits-small-business/
NOT https://www.belveb.by/deposits/

Neo ЮЛ:
https://neobank.by/business/razmeshchenie-sredstv/depozity/
NOT https://neobank.by/deposits/

RRB ЮЛ:
https://www.rrb.by/korporativnim-klientam/depoziti
NOT homepage crawl

Technobank ЮЛ:
https://tb.by/business/investments/deposits/
```

Все текущие hypotheses заново live-profile перед VERIFIED.

### Regression anchors

- TC Bank: HTML matrix → generic unpivot.
- Paritet: old/current dated documents → effective revision resolver.
- BelVEB/Neo: sourceRole collision tests.
- Technobank/JS-heavy: false shell success protection.

---

# 15. Story BMD-012 — build all ФЛ presets + workflows

Используй `PASSPORT_FL_DEPOSITS.md`.

Primary official banks:

```text
Belarusbank
Belagroprombank
Belgazprombank
Belinvestbank
Bank Dabrabyt
MTBank
BNB Bank
Priorbank
Paritetbank
BelVEB
VTB Belarus
Technobank
StatusBank
Neo Bank
RRB
Alfa-Bank
Zepter Bank
R-Bank
BSB Bank
```

Secondary reconciliation:

```text
MyFin deposits — authority=SECONDARY
```

MyFin никогда не перезаписывает official primary record.

Regression anchors:

- RRB detail: rich retail field coverage;
- StatusBank: filters + revocability/detail;
- BelVEB/Neo: retail-vs-corporate assertions;
- JS-heavy routes: explicit fallback + shell checks.

---

# 16. Story BMD-013 — build news presets + workflows

Используй `PASSPORT_MARKET_NEWS.md`.

Required primary/web source presets:

```text
BCSE releases
BCSE news
NBRB press
NBRB statistics allowlist
Ministry of Economy
Ministry of Finance
Central Depository
PrimePress analytics
PrimePress finance
MyFin securities
MyFin precious metals
MyFin analytics
Phoenix Refining
Business Times precious metals/gold
TexMetals
```

BCSE numeric rates/REPO → indicators dataset.

Telegram:

```text
t.me/s/jsc_bcse
t.me/s/pressnbrb
t.me/s/minfinrb
t.me/s/econ_gov_by
```

как SECONDARY/TEST, где mirror anonymous-public.

`web.telegram.org` не автоматизировать.

### Required patterns

- BCSE: API-first where public endpoint live-confirmed; browser fallback only.
- Centraldepo: `?PAGEN_1=N` + date boundary + detail.
- PrimePress: URL pagination, paid metadata-only, bank-topic rule for finance.
- MyFin: strict category container, no `Популярное` leakage, JSON-LD→DOM detail.
- Phoenix: `?page=N`, gold/silver/platinum selection.
- BusinessTimes: paywall-safe.
- TexMetals: direct page URL if possible, browser fallback, repeated page hash.

---

# 17. Site profiling procedure — mandatory before VERIFIED

Для каждого source:

1. GET seed URL through the same safe egress policy Multiverse will use;
2. record redirect chain/final URL;
3. canonicalize URL;
4. inspect status/MIME/content size;
5. run postconditions for meaningful representation;
6. inspect server DOM;
7. inspect JSON-LD/microdata;
8. inspect only **public anonymous** XHR/fetch traffic when dynamic;
9. discover RSS/XML/API if public;
10. discover tabs/filter states;
11. discover pagination and stable URL pattern;
12. discover detail fan-out;
13. inventory only scoped product/article documents;
14. record source role/authority;
15. capture fixture(s);
16. create preset as DRAFT;
17. run fixture test;
18. run live smoke;
19. only then VERIFIED.

Do not conclude `browser-only` from one failed HTTP request or stale audit note.

---

# 18. Security/legal boundary

Hard requirement inherited from Multiverse philosophy:

Parser only processes content that is publicly available to a normal anonymous visitor.

Do not bypass:

```text
login
authenticated account
CAPTCHA
paywall
robots/source policy restrictions
private APIs
private networks/internal IPs
session-bound Telegram Web
technical access controls
```

Redirect/public alternate endpoint is allowed only when:

- it is publicly accessible;
- source policy allows it;
- final source is fully captured in provenance.

Public availability does not automatically grant redistribution rights. Preserve source/license provenance; deployment owner is responsible for downstream rights review.

---

# 19. Data API contract для внешнего ИИ

## ЮЛ

```http
GET /api/v1/datasets/deposit-offers-legal/coverage
GET /api/v1/datasets/deposit-offers-legal/records?view=current
```

## ФЛ

```http
GET /api/v1/datasets/deposit-offers-retail/coverage
GET /api/v1/datasets/deposit-offers-retail/records?view=current
```

## News

```http
GET /api/v1/datasets/market-news/coverage?from=<ISO>&to=<ISO>

GET /api/v1/datasets/market-news/records
  ?view=current
  &time_basis=source_published_at
  &from=<ISO>
  &to=<ISO>
```

## Optional evidence

```http
...&include=evidence
```

или record evidence endpoint согласно реализованному API design.

## Consumer rule

External AI **сначала проверяет coverage**.

```text
PASS    → использовать как полный snapshot/window
PARTIAL → использовать с explicit missing-source warning/policy
FAIL    → не выдавать результат как полный
```

---

# 20. Scheduling

## Deposits

Бизнес-паспорт: еженедельно по понедельникам или on-demand.

Реализовать:

- weekly schedule;
- manual/on-demand runs;
- independent workflow per source;
- failure одного банка не отменяет observations других.

## News

Рабочий orchestration должен запускаться к утреннему дайджесту.

Не кодировать `yesterday` в parser.

Caller/scheduler формирует exact interval:

```text
Tue–Fri: previous calendar day
Mon: Friday 00:00 Europe/Minsk → Monday 00:00 Europe/Minsk
```

Если фактическое бизнес-окно отличается — interval передаётся параметром Start.

---

# 21. One workflow per source

Не создавать один гигантский graph, который содержит все банки.

Предпочтительно:

```text
source preset A → workflow A ─┐
source preset B → workflow B ─┼→ shared dataset
source preset C → workflow C ─┘
```

Плюсы:

- fault isolation;
- independent schedules/retries;
- clear source-level coverage;
- precise provenance;
- independent preset revisions;
- проще live smoke и rollback.

---

# 22. Output/versioning semantics

Output commit только после Assure.

Требования:

- failed/partial policy explicit;
- natural key stable;
- content hash/version history;
- existing current record не удаляется из истории из-за transient source failure;
- новый meaningful value → RecordVersion;
- unchanged fetch → observation;
- source timestamp/fetched timestamp/observed timestamp разделены;
- raw artifact retained according to evidence policy.

---

# 23. Generic Definition of Done каждого source preset

Preset/workflow считается production-ready только если:

1. canonical/final URL доказан;
2. authority/source role доказаны;
3. chosen Acquire strategy объясним;
4. fallback attempts сохраняются;
5. selectors scoped к нужной collection;
6. pagination bounded и имеет stop reason;
7. tabs/filter states reconciled;
8. detail fan-out reconciled:

```text
discovered = succeeded + intentionally_skipped + failed + duplicate
```

9. attachments scoped к parent record;
10. current revision resolved or explicit ambiguity;
11. raw + normalized fields доступны;
12. field evidence сохраняется;
13. schema/required coverage проходят;
14. natural key стабилен;
15. expected empty vs unexpected empty различаются;
16. fixture test проходит;
17. live smoke проходит;
18. second unchanged run produces observation, not duplicate version;
19. changed rate/article creates expected version/update;
20. source failure отражается coverage;
21. no source-specific engine code;
22. `VERIFIED` имеет fixture ref.

---

# 24. Test strategy

## Unit

Generic transforms/resolvers/assertions, без реальных доменов.

## Fixture regression

Для каждого SourcePreset минимум один retained fixture. Для сложных источников — несколько:

```text
normal current page
effective/current + archive doc
empty shell/access change
pagination page 1/2
changed layout representative
```

## Live smoke

Не делает тестовый suite зависимым от интернета целиком. Separate opt-in/live suite:

- status/meaningful representation;
- source role;
- min business records or valid empty window;
- no forbidden access behavior.

## End-to-end

Минимум четыре E2E scenarios:

1. TC Bank legal matrix → dataset → API;
2. rich retail bank detail → dataset → API;
3. current/archive document bank → correct current version;
4. paginated news interval → date boundary → coverage → Data API.

---

# 25. Recommended source rollout

Не начинай с самых сложных JS sites.

### Wave 1 — validate generic pipeline

```text
TC Bank legal             — HTML matrix/unpivot
MTBank legal              — rich HTML/product semantics
RRB retail                — rich detail fields
NBRB press                — simple list/detail
Central Depository        — page pagination/date boundary
```

### Wave 2 — API/document cases

```text
BCSE
Belarusbank
Paritetbank
Sber Bank
Belinvestbank
```

### Wave 3 — broad simple HTML banks/news

Оставшиеся rich server-rendered sources.

### Wave 4 — dynamic/fallback sources

```text
Technobank
Neo representations
Dabrabyt unstable representations
TexMetals if JS-required
other sources only after live profiler proves browser/XHR need
```

---

# 26. Pull request plan

## PR-1 — Baseline

- baseline SHA/tests;
- delta report;
- no unnecessary architecture rewrite.

## PR-2 — Collection transforms

- explode;
- matrix_to_records/unpivot;
- expand_tiers;
- evidence retention;
- generic fixtures.

## PR-3 — Date-bound traversal

- dateBoundary;
- DATE_BOUNDARY_REACHED;
- ordering safety;
- tests.

## PR-4 — Effective revision/document resolver

- candidate ranking;
- date semantics;
- ambiguity/review;
- fixtures.

## PR-5 — Assure extensions + budget audit

- field coverage;
- expected states;
- source role;
- detail/doc ratios;
- date window;
- count drift;
- deadline semantics verified/fixed.

## PR-6 — Coverage + evidence API

- generic dataset coverage;
- source membership;
- window coverage;
- explicit evidence contract;
- auth/backward compatibility tests.

## PR-7 — Belarus preset pack/importer

- schemas;
- policies;
- idempotent import if needed;
- fixture structure.

## PR-8 — ЮЛ pack

- all required presets;
- fixtures;
- workflows;
- dataset wiring.

## PR-9 — ФЛ pack

- all retail presets;
- MyFin secondary reconciliation;
- workflows.

## PR-10 — News pack

- all primary web presets;
- deterministic rules;
- Telegram secondary/test where public;
- market indicators split.

## PR-11 — Schedule / consumer API / auth

- scoped read-only API token;
- schedules;
- coverage-first integration example.

## PR-12 — Final regression/live QA

- full fixture suite;
- smoke matrix;
- docs;
- final source status report.

---

# 27. Final deliverables

EPIC нельзя закрывать, пока в repository нет:

```text
[ ] generic capabilities code
[ ] migrations if required
[ ] unit tests
[ ] generic fixtures
[ ] deposit schema
[ ] news schema
[ ] indicator schema if implemented
[ ] declarative source presets
[ ] per-source fixtures
[ ] instantiated workflows or reproducible bootstrap that creates them
[ ] shared datasets
[ ] schedules
[ ] coverage API
[ ] evidence API/contract
[ ] read-only consumer auth example
[ ] E2E tests
[ ] live smoke command
[ ] source status matrix
```

---

# 28. Final source status matrix required from agent

Сформируй файл, например:

```text
docs/belarus_market/SOURCE_STATUS.md
```

Columns:

```text
source_id
name
segment/category
canonical_url
preset_slug
preset_revision
workflow_id
primary_strategy
fallbacks
fixture_ids
live_smoke_at
status (VERIFIED/DRAFT/BLOCKED)
current_record_count
coverage_status
known_limitations
```

`DRAFT/BLOCKED` — нормальный честный результат, если source невозможно доказать публично/анонимно.

Ложный `VERIFIED` недопустим.

---

# 29. Completion report format

После реализации coding-агент должен отдать краткий отчёт:

## Engine changes

Что было добавлено и почему capability универсальна.

## Existing capabilities reused

Что обнаружено в current `main` и не дублировалось.

## Datasets/API

Schemas, endpoints, auth, example calls.

## Sources

```text
Verified: N
Draft: N
Blocked: N
```

Для каждого DRAFT/BLOCKED — конкретная техническая причина.

## Tests

```text
unit: pass/fail
integration: pass/fail
fixture regression: pass/fail
live smoke: pass/fail/partial
```

## Risks

Только конкретные unresolved issues, без общих фраз.

---

# 30. Acceptance criteria EPIC

EPIC принят, если внешний ИИ может выполнить следующий сценарий, **не зная внутренних особенностей сайтов**:

```text
1. GET coverage ЮЛ
2. GET current ЮЛ records
3. GET coverage ФЛ
4. GET current ФЛ records
5. вычислить news interval
6. GET news coverage interval
7. GET news records interval
8. при спорном значении запросить evidence
```

и при этом Multiverse способен объяснить для любого record:

```text
где найдено
когда найдено
каким preset revision
какой strategy сработал
какие fallback были
какое raw значение
как оно нормализовано
прошла ли полнота source
почему output был разрешён
```

Если это выполняется без bank/site-specific engine code — задача соответствует философии Multiverse.
