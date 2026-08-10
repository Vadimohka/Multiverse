# Multiverse Forensic Audit

Дата аудита: 2026-08-11  
Исследованный публичный tip: `origin/main` = `ecf5e681`  
Release baseline: `f7b4f25` (`chore: prepare Multiverse for public release`)

## Executive conclusion

`f7b4f25` доказан как первый публичный release commit: это корневой commit доступной истории, tag отсутствует, более ранних refs/reflog/unreachable objects нет. Однако он является release baseline, а не «последним архитектурно здоровым» состоянием. Уже в нём generic source template использует `.product-card` и банковские поля, а `crawl_links` ориентирован на новости BCSE. В доступной истории нет commit, который одновременно был бы универсальным, end-to-end полным и проходил бы современный contract.

Лучшее направление после релиза появилось в `0f6ba26`: workflow впервые строится из сохранённого profile источника. `297856b` исправил конкретный Belinvestbank, но закрепил CSS/id/банковскую семантику в core. `536b755` существенно улучшил pipeline lifecycle и review management, одновременно добавив новые финансовые эвристики. `d7b2444` правильно удалил часть этих special cases и добавил list-detail/browser capabilities, но заменил их неполной article-specific реализацией, добавил BCSE marker filtering и впервые сделал suite красным.

Текущее состояние не предоставляет требуемый Data API. `RecordVersion` создаётся только при изменении payload, поэтому «records последнего успешного run» нельзя корректно восстановить для неизменившихся records. `source_published_at`, `source_modified_at`, `fetched_at` и связь version/observation с `RawDocument` в реляционной модели отсутствуют.

## Evidence policy

В этом отчёте:

- **Fact** — подтверждено commit/diff/code/test/runtime;
- **Inference** — вывод из нескольких фактов, но intent не задокументирован автором;
- **Hypothesis** — требует дополнительного runtime/product подтверждения.

## Repository and history inventory

На старте локальный checkout был чистым. Локальный `main` находился на `e401b3c`, отслеживал `origin/main`, был ahead 1 / behind 10. `git fetch --all --tags --prune` безопасно обновил remote refs; локальные refs и worktree не переписывались.

- Remote: `origin = https://github.com/Vadimohka/Multiverse.git`.
- Tags: отсутствуют.
- Root/public release: `f7b4f25`, 2026-08-02 15:18:13 +03:00.
- Remote main at audit: `ecf5e68`, 2026-08-10 23:57:54 +03:00.
- `git reflog --all`: не содержит pre-release commit.
- `git fsck --no-reflogs --unreachable --full`: unreachable objects не найдено.
- Codex capture ref `77f415…` найден, но не содержит отдельной потерянной реализации.

Исследованы `status`, remotes, все local/remote branches, `show-ref`, full graph/log, fuller dates, reflog, merge bases, per-branch diffs, per-file history/blame и commit diffs. Destructive Git commands не выполнялись.

## Baseline determination

| Candidate | Evidence for | Evidence against | Decision |
| --- | --- | --- | --- |
| `f7b4f25` | Root commit; message прямо фиксирует public release; README/implementation material уже присутствует; 36 tests pass в изолированном Python 3.14 environment | Нет tag; core уже содержит `.product-card`, bank schema и BCSE/news assumptions | Фактический public release baseline, но не healthy architecture |
| `0f6ba26` | Удаляет `.product-card` из generated workflow; использует profile; 41 tests pass | Profiler и transforms всё ещё выбирают banking fields по vocabulary | Хорошая точка восстановления отдельных mechanisms, не baseline целиком |
| `536b755` | 44 tests pass; lifecycle/review/template management значительно полнее | Business semantics в mapping/LLM/dedup/crawl; ещё нет Data API/provenance contract | Последний commit с зелёным собственным suite, но не архитектурно здоровый |
| `d7b2444` / `ecf5e68` | Удаляет часть финансовых special cases; добавляет crawler/browser list-detail | 2 tests fail; fixed article schema, BCSE markers, hidden defaults, API gaps | Текущий repair base, не healthy baseline |

**Conclusion (Fact):** доступная история не содержит доказанного «последнего хорошего Multiverse» в полном продуктовом смысле. Поэтому восстановление должно быть selective/manual: сохранить generic mechanisms из разных commits, а не откатывать repository к одному SHA.

## All significant changes after release

| Commit | Что изменили | Зачем / исходная проблема | Что стало лучше | Что стало хуже | Regression risk | Generic / site-specific |
| --- | --- | --- | --- | --- | --- | --- |
| `97ad5d7`, `e72d36f`, merges `4491889`, `93850a1`, `01c9cbd` | Python 3.14 dependencies и verification docs | Платформа должна устанавливаться на Python 3.14 | Актуализирован runtime contract | В system Python с несовместимым FastAPI app не импортируется; это environment mismatch, не code regression | Low | Generic infrastructure |
| `b6a9dea`, merges `e401b3c`, `b610b41`, `0950f5e`, `b157d1e` | Minimal compose profile | Запуск на low-resource host | Более практичный self-host deployment | Functional parser architecture не меняется | Low | Generic infrastructure |
| `0f6ba26` | Source profiler config сохраняется и используется generator; follow-links/detail table/natural key/on-empty | Baseline generator всегда создавал `.product-card`, независимо от source | Source selectors стали config-driven; появился list→detail contract | Field inference и transforms используют banking vocabulary; candidate scoring слабый | High | Mixed: generic capability + domain heuristic leakage |
| merge `cb7d068` | PR #4 integration | Доставить universal source parser fix | То же | То же | High | Mixed |
| `297856b` | Belinvest selectors/scoring, `product_name`, detail table aliases, bank table normalization | Profiler выбирал не тот repeating block и не получал данные detail page Belinvestbank | Конкретный fixture стал проходить; 44 tests pass | Core узнаёт `services-item`, `js-service-item`, `item-description-link`, `deposit_name_` и банковские имена | Critical | Site-specific core hardcode |
| merge `4f72901` | PR #6 integration | Доставить Belinvest fix | То же | То же | Critical | Site-specific |
| `536b755` | Reusable workflow templates, import/export, publish counter, review policy/baseline, batch sources, queue/status UI | Pipeline management был неполным, version/publish/review paths расходились | Много корректных lifecycle fixes; 44 tests pass | `customer_type` из source names; finance-focused LLM/link/dedupe; fixed `sFrom/sTo`; implicit HTTP→browser | Critical | Mixed |
| `d7b2444` | Universal list-detail crawler, browser tab exploration, template cleanup, removal of several financial branches | Вернуть general crawler и убрать banking special cases | Удалены Belinvest rename/default table, Mapping source-name semantics, NBRB/finance LLM helpers; configurable patterns/same-origin; browser detail path | Candidate root regression; 2 tests fail; tabs включены implicit default; BCSE marker filters; article-specific record schema; query stripping; UI config incomplete | Critical | Mixed, still site/article-specific |
| merge `ecf5e68` | PR #8 integration | Доставить crawler | Current main содержит capabilities | Current main красный; PR discussion содержит сообщение о поломке, что является signal, а не proof | Critical | Mixed |

## Regression map

| Transition | Приобретено | Потеряно / сломано | Proof |
| --- | --- | --- | --- |
| Release → `0f6ba26` | Profile-driven selectors, details, natural keys, explicit empty handling | «Универсальность» всё ещё подменена банковским field vocabulary | Diff `f7b4f25..0f6ba26`; 36→41 passing tests |
| `0f6ba26` → `297856b` | Belinvest fixture и detail extraction | Generic profiler/scoring и workflow generation стали site-aware | Diff/commit `297856b`; 44 tests pass only because site behavior is asserted |
| `297856b` → `536b755` | Workflow/template/review/batch management | Новые finance/NBRB/source-name special cases; hidden fetch behavior | Diff `297856b..536b755`; 44 tests pass |
| `536b755` → `d7b2444` | Generic-looking crawler, browser tabs, removal of several special cases | Repeating container regression; site-marker blocking; implicit tabs; fixed article payload | `d7b2444` suite: 2 failures; current main: same 2 failures |
| `d7b2444` → current `ecf5e68` | Merge only | Regression preserved | Tree/content equivalent for relevant files; 51 pass / 2 fail |

## Local branches not represented in main

Таблица относится к refs, существовавшим **до** создания audit worktree.

| Branch | Merge base with `origin/main` | Unique commits | Что реализовано | Лучше ли main | Что вернуть | Способ |
| --- | --- | --- | --- | --- | --- | --- |
| `main` (`e401b3c`) | `b6a9dea` | `e401b3c` merge commit | Локальное merge minimal-compose | Нет: tree совпадает с публичным `b610b41`; функционально уже в `origin/main` | Ничего | already superseded |
| `feat/minimal-compose` (`b6a9dea`) | `b6a9dea` | 0 относительно `origin/main` | Low-resource compose | Уже в main | Ничего | already superseded |
| `codex/universal-source-parser` (`0f6ba26`) | `0f6ba26` | 0 | Profile-driven generation | Часть design правильнее baseline; код уже в ancestry main | Сохранить config-driven contract, не branch целиком | already superseded / manual refinement |
| `chore/post-ci-verification` (`93850a1`) | `93850a1` | 0 | Verification docs | Уже в main | Ничего | already superseded |

Remote `origin/chore/python-3.14` также полностью представлен в main. Удалённые feature branch names (`belinvest-source-parser`, `feature/universal-crawler`) не существуют как local heads, но их commits достижимы через merges `4f72901` и `ecf5e68` и полностью исследованы.

**Result (Fact):** хорошей реализации, существующей только в локальной ветке или dangling commit, не найдено. Cherry-pick не рекомендован. Полезные части должны переноситься вручную из reachable commits, поскольку commits смешивают generic и domain-specific изменения.

## Site-specific logic found in generic core

### Belinvest repeating-container scoring (removed later, tests remain)

- **File/function at introduction:** `apps/api/app/services/source_profiler.py`, candidate score; `297856b`.
- **Original intent:** заставить profiler выбрать outer service card вместо вложенной ссылки.
- **Why site-specific:** tokens `services-item` и `js-service-item` — классы одного сайта.
- **Missing generic capability:** structural similarity, usable descendant fields, DOM depth/content density and selector stability scoring.
- **Replacement:** score repeated containers by sampled structural consistency and extractability; link itself не должен выигрывать, если configured child selectors невозможно применить относительно self.
- **Regression test:** neutral classes, two competing repeated groups, stable content-card group must win.

### Belinvest field rename/detail defaults (removed later, stale test remains)

- **File/function at introduction:** `apps/api/app/services/workflow_templates.py`; `297856b`.
- **Original intent:** produce expected bank dataset fields and detail table.
- **Why site-specific:** `item-description-link`, `deposit_name_`, `product_name`, rate/term/currency aliases.
- **Missing generic capability:** user-defined canonical field mapping and configurable detail schema.
- **Replacement:** preserve profiler names; expose mapping/detail fields in UI; site preset may map `title → product_name`.
- **Regression test:** arbitrary `headline → name` mapping configured by workflow, with no vocabulary inference.

### Financial table-field normalization

- **File/function:** `packages/workflow_engine/nodes.py::normalize_table_field_name`; introduced in `297856b`, still present.
- **Original intent:** normalize scraped bank detail labels.
- **Why site-specific:** detects currency/rate/term/product semantics from Russian/English finance vocabulary.
- **Missing generic capability:** configurable header mapping, regex mapping and normalizer operations.
- **Replacement:** generic key sanitization only in core; semantic mapping belongs to workflow/preset.
- **Regression test:** unrelated table headers preserve generic keys; configured mapping produces desired domain fields.

### Bank-record deduplication

- **File/function:** `packages/workflow_engine/nodes.py::dedupe_extracted_records`; introduced in `536b755`, still present.
- **Original intent:** remove duplicate LLM-extracted deposit offers.
- **Why site-specific:** hardcoded tuple `bank_name`, `product_name`, `currency`, `source_url`.
- **Missing generic capability:** configured natural/dedupe keys plus optional content hash.
- **Replacement:** require `dedupe_key_fields` or dataset natural keys; otherwise use stable payload hash only when explicitly selected.
- **Regression test:** non-financial records dedupe by configured compound key.

### BCSE query parameters

- **File/function:** `packages/workflow_engine/nodes.py::CrawlLinksNode.run`, `lookback_days`; inherited from `536b755`, still present.
- **Original intent:** populate BCSE calendar `sFrom`/`sTo`.
- **Why site-specific:** fixed query names/date format.
- **Missing generic capability:** configurable query parameter templates derived from run clock.
- **Replacement:** `query_params` template with timezone/date-format expressions; BCSE preset supplies keys.
- **Regression test:** arbitrary `startDate/endDate` config and no implicit query mutation.

### BCSE/article DOM selectors and record schema

- **File/function:** `packages/workflow_engine/nodes.py::extract_article_record`; baseline `f7b4f25`, extended through `d7b2444`.
- **Original intent:** extract BCSE news consistently.
- **Why site-specific:** `.dynamic-publicationdate`, `#pc_body`, article/news fields and attachment representation are built into crawler output.
- **Missing generic capability:** configured detail field extractors, metadata extraction and reserved timestamp mapping.
- **Replacement:** generic detail extractor; optional `article` preset/template contains those selectors.
- **Regression test:** two unrelated detail schemas from the same crawler primitive.

### BCSE marker-based workflow blocking/cleaning

- **File/functions:** `apps/api/app/routers/workflows.py::_is_legacy_site_workflow`; `workflow_templates.py::_contains_site_binding/_clean_graph`; introduced `d7b2444`.
- **Original intent:** hide/disable legacy site-bound default graphs and expose a generic template.
- **Why site-specific:** regex `bcse|бвфб|press_center|bcsenews`; also treats literal URL/configured selector as invalid in contexts where site configuration is legitimate.
- **Missing generic capability:** explicit template portability metadata and migration version, distinct from executable workflow config.
- **Replacement:** `scope = SYSTEM_TEMPLATE | PROJECT_TEMPLATE | EXECUTABLE`, `portable` flag, schema-version migrations; never inspect business strings.
- **Regression test:** executable site-bound workflow remains runnable; portable template with bindings is rejected by structural validator.

### Language/region defaults

- **File/functions:** Browser and crawler headers/config default `ru-RU`, profile defaults `Europe/Minsk`; current main.
- **Original intent:** reliable Belarusian/Russian sites.
- **Classification:** regional product default is acceptable in an example/profile, but it is a core behavior leak when every request/browser silently receives it.
- **Missing generic capability:** source/browser-profile locale, timezone and headers passed explicitly.
- **Replacement:** neutral core defaults; project/source presets can retain current regional values for backward compatibility.
- **Regression test:** configured locale/header propagation and no hardcoded locale in low-level client.

### Correctly isolated domain material

`seed_templates.py`, demo endpoint/bootstrap, `bank_deposits` project option and BCSE fixture tests are class **D (example/template/fixture)** when they do not influence generic selection. They should be moved/labelled as examples, not simply deleted. `normalize_currency` is class **C (domain helper)** because it is selected by an explicit transform operation; it may remain.

## Source Profiler audit

| Capability | Current state | Finding |
| --- | --- | --- |
| Repeating candidates | Groups by class combinations and count | No sampled structural similarity/content density/field population confidence |
| Selector generation | CSS class combinations | Compound selector may overmatch; invalid/unescaped class candidates are dropped; XPath is not produced |
| Field suggestion | Inspects first candidate | Business vocabulary drives title/rate/term/currency suggestions; consistency across instances not measured |
| List/detail | Presence of links toggles follow-links | No confidence, allowed-domain or canonical policy; link can incorrectly become container |
| Table | Counts tables | Does not generate a robust table extractor proposal |
| Pagination | Link/form counts only | No next/query/tab strategy proposal contract |
| JS/browser fallback | Text/script heuristic | Useful generic heuristic, but implicit mode switching blurs explicit workflow behavior |
| JSON/XHR | Captures and sanitizes XHR previews | Good capability; schema/JSONPath suggestions remain shallow |
| Publication metadata | JSON-LD count only | No generic reserved timestamp extraction config |

Current failed profiler test reveals a real generic root cause even though the test is site-named: `d7b2444` prioritizes a repeated direct `<a>` over its repeated card parent. `ExtractRepeatingList` selectors are evaluated on descendants of each container, so a link container cannot extract itself with the generated selector. Restoring the `services-item` bonus would only restore the symptom fix.

## Crawler audit

### Generic pieces worth keeping

- relative URL resolution;
- bounded concurrency and retry count;
- same-origin and URL-pattern options in `crawl_links`;
- explicit `max_pages`/`max_depth` bounds;
- browser network capture and raw artifact storage;
- parent/child merge modes in `follow_links`;
- page/tab signatures preventing simple browser cycles.

### Broken or incomplete contracts

- `canonical_url()` removes all query parameters; query-identified records and query pagination collapse incorrectly.
- Browser tab exploration defaults to enabled, changing old `browser_open` behavior without config.
- `CrawlLinksNode._load_listing()` does not forward tab options; the system template works only because of the unsafe global default.
- `follow_links` deduplicates raw URL strings only and has no same/allowed-domain contract.
- `pagination` returns page bodies but workflow has no clear fan-out/subgraph primitive to extract every page.
- `crawl_links` is an article crawler with fixed output fields, not a universal list→detail orchestration primitive.
- Global timeout, rate-limit/`Retry-After`, shared cookies/session, canonical query policy and durable recovery are incomplete.
- Each browser detail may launch a fresh browser, losing session state and scaling poorly.
- Empty/partial failure semantics are inconsistent across nodes.

## End-to-end contract audit

| Stage | Input → output today | Contract break / data loss |
| --- | --- | --- |
| Source | DB Source + settings/profile → workflow variables | Profile schema is informal JSON; UI and generator fields are not versioned |
| Profiler | HTML/browser/XHR → profile JSON | Vocabulary/site bias; weak confidence; XPath/timestamp extraction not represented |
| Workflow generation | Profile → graph | Some config preserved, some inferred/rewritten; site markers mutate/block graphs |
| Fetch/browser | source URL/settings → `html/json/artifacts` | Browser fallback implicit; fetched timestamp not first-class; headers/locale leaked |
| Pagination/crawl | listing → pages/details | Query canonicalization loses identity; article-fixed schema; hidden tab defaults |
| Extraction | HTML/JSON → records | Self/container mismatch; publication field not canonical; metadata conventions vary |
| Transform/validation/mapping | records → records/errors | Generic transforms exist; semantic table/dedupe inference violates boundary |
| Deduplication | records → unique records | Hardcoded bank key in LLM path; dataset natural key not the universal contract |
| Persistence/versioning | output records → Record/RecordVersion | Version only on content change; unchanged record has no per-run observation |
| Review | pending version/task → current Record | Review timestamps can change entity `updated_at`; must not be treated as observed time |
| Dataset | current approved Record rows | Pending/current semantics exist, but run membership/provenance cannot be queried |
| API/export | Record/current + history | No run/time/source timestamp filters; unstable offset ordering; no cursor/meta contract |

`run_events` recognises only a subset of terminal statuses and omits `SUCCESS_EMPTY_ALLOWED` / `SUCCESS_EMPTY_UNEXPECTED`; SSE clients can continue polling after a completed empty run. Empty result is therefore both a workflow-policy concern and an API lifecycle regression.

## No-code editor audit

Backend capability is not considered complete unless a user can configure it. Current UI can configure CSS selectors, field mappings, transform operations, dataset output and basic pagination/follow-links. Gaps:

- selector picker returns CSS only although acceptance docs claim CSS/XPath;
- browser actions, detail table and several mappings are raw JSON editors, not safe guided no-code controls;
- crawler tab settings, listing fetch mode/waits, canonical query policy, allowed domains, arbitrary detail schema and publication timestamp parsing are absent from catalog;
- system template embeds hidden fields not exposed in catalog;
- workflow run requires a selected source even for source-independent graphs;
- dataset UI preferred columns are banking-specific, so arbitrary datasets can render poorly;
- legacy workflow string matching hides legitimate configured site workflows instead of migrating templates structurally.

## Data model, versioning and provenance

### Current facts

- `Dataset` has id, slug and natural key fields.
- `Record` stores the current snapshot/status.
- `RecordVersion` stores payload/hash/version, optional `run_id`, `observed_at`, review state and `created_at`.
- `Run` stores source/workflow and started/finished timestamps.
- `RawDocument` stores run/source/url/hash/storage metadata.
- Changed/new payload creates a version; unchanged payload increments run counters but creates no version.
- Stable hashing excludes several transient artifact/evidence fields, which is directionally correct.

### Missing facts required for every observation

- no relational `source_published_at`;
- no `source_modified_at`;
- no unambiguous `fetched_at`;
- no per-run observation for unchanged records;
- no direct `RecordVersion`/observation → `RawDocument` proof link;
- no indexed way to answer latest successful run membership.

**Root cause:** content versioning and run observation were treated as one event. A new immutable content version should be created only when content changes, while an observation should be created for every record seen in every successful run. Without a separate observation relation, Scenario B is impossible without duplicating versions or reading opaque run output.

## Current Data API gaps

Current endpoints provide dataset list, offset-based current approved records, individual record history and exports. `GET /datasets/{id}/records` orders by `updated_at DESC`, which is neither stable on ties nor a source/parser timestamp.

Missing:

- lookup by dataset slug;
- explicit `view=current`;
- latest successful run and `run_id` views;
- unchanged-record membership in a run;
- `time_basis=source_published_at|observed_at|fetched_at`;
- `from`, `to`, exact-second `at`;
- timezone-aware response guarantee;
- stable keyset/cursor pagination and response metadata;
- provenance/raw artifact references;
- a practical scoped read-only M2M credential;
- complete OpenAPI response/error contract.

JWT user access (short-lived access + refresh and roles) is suitable for UI sessions but inconvenient for server integrations. A scoped, hashed, revocable read-only API token/service account can be added without replacing JWT; implementation requires a separate security review.

## Timestamp semantic audit

| Timestamp | Required meaning | Current state |
| --- | --- | --- |
| `source_published_at` | Source originally published information | Missing; crawler emits ambiguous payload `published_at` |
| `source_modified_at` | Source-declared modification time | Missing |
| `fetched_at` | HTTP/browser/document bytes obtained | Missing as first-class relational field |
| `observed_at` | System fixed a record observation/version | Present on `RecordVersion`, but not on unchanged per-run observations |
| `created_at`, `updated_at` | Internal entity lifecycle | Present; currently API ordering risks semantic misuse |
| `run.started_at`, `run.finished_at` | Workflow execution boundaries | Present |

All new timestamps must be timezone-aware UTC at storage/API boundaries. `updated_at` must never substitute for publication/fetch/observation time. Exact-second matching must be a half-open UTC range `[at, at + 1 second)` so microseconds are retained.

## Documentation versus reality

| Documented claim | Reality | Classification |
| --- | --- | --- |
| Source creation produces a source and ready workflow | Current source UI creates a source; automatic workflow behavior changed | Stale documentation |
| CSS/XPath selector picker ready | Picker returns CSS selector candidate only | False/partial |
| Data slug used in API | Records endpoint accepts dataset id only | Missing API |
| Universal list-detail | Current `crawl_links` persists a fixed article/news schema | Fixture/template-level capability |
| Runtime verification/smoke | Current smoke searches removed `Демо-парсер депозитов` and fails | Regression in verification asset |
| Implementation report passing suite | Current backend 51 pass/2 fail; both linters fail | Stale evidence |
| Architecture/API documented | `docs/architecture.md` is skeletal; `docs/api.md` only points to OpenAPI UI | Incomplete |

## Verification before repair

| Command | Result | Classification |
| --- | --- | --- |
| `make test` with system Python 3.13 | Import failure due old FastAPI 204 assertion | Environment mismatch; project requires Python 3.14/current requirements |
| `make test` in Python 3.14 venv | 51 passed, 2 failed | Product regression in profiler/template tests |
| Historical test at `f7b4f25` | 36 passed | Fact |
| Historical test at `0f6ba26` | 41 passed | Fact |
| Historical test at `297856b` | 44 passed | Fact; includes site-specific behavior |
| Historical test at `536b755` | 44 passed | Fact |
| Historical test at `d7b2444` | 2 failed | First observed regression commit |
| `make lint` | 4 errors: imports (2), E731 lambda, test import | Code/static regression |
| `npm ci && make frontend-lint` | 2 unused-symbol errors | Code/static regression |
| `make frontend-build` after `npm ci` | Success; bundle-size warning | Pass |
| `python scripts/smoke_test.py` | `StopIteration`: removed demo workflow expected | Stale smoke contract / regression |
| `docker compose config --quiet` with temporary `.env` | Success | Pass; initial missing `.env` was environment setup |

## Root-cause priorities

### P0

- Make run/dataset observation model capable of current, latest-run and timestamp queries without semantic timestamp reuse.
- Fix generic repeating-container selection and remove stale site-specific assertions.
- Ensure all terminal empty statuses terminate run event streaming.

### P1

- Replace bank dedupe/table inference and BCSE query/article/marker logic with explicit generic config plus isolated presets.
- Make URL canonicalization query-aware and crawl/browser behavior explicit.
- Preserve provenance from source fetch/raw artifact through observation/version.

### P2

- Expose the backend contract through guided no-code controls.
- Implement stable Data API, exact-second filters, cursor pagination, OpenAPI/error schemas and read-only integration auth.
- Restore smoke/lint/docs consistency.

### P3

- Improve profiler confidence/scoring breadth, pagination proposals, selector stability and JSON schema suggestions.
- Add performance/rate-limit/session recovery improvements after contracts are stable.

## Component disposition

| Component | Decision | Reason |
| --- | --- | --- |
| DAG executor and typed node registry | GOOD | Reusable orchestration foundation |
| Source profile persisted into source config | GOOD | Correct configuration boundary |
| Artifact storage/network capture | GOOD | Valuable evidence mechanism; needs relational linkage |
| Dataset natural keys/review/version snapshots | REFACTOR | Good base, missing per-run observation/provenance |
| Profiler candidate and field inference | REFACTOR | Structural score too weak; business vocabulary leaked |
| Belinvest bonuses/renames | REMOVE after generic replacement | Symptom fixes |
| Pipeline lifecycle/review fixes from `536b755` | RESTORE/PRESERVE selectively | Correct behavior mixed with domain logic |
| `crawl_links` | REFACTOR | Useful bounds/concurrency, fixed article model |
| BCSE marker cleaner/blocker | REMOVE after metadata migration | Site-specific control flow |
| Example bank/BCSE templates | GOOD, isolate | Valid presets/fixtures, not core behavior |
| Data API observation/run/time contract | MISSING | Core product requirement not represented |
| Guided crawler/timestamp UI | MISSING | Backend-only/raw JSON is not no-code completeness |

## Audit limits

- Live external sites were intentionally not used as correctness dependencies.
- No tag proves a marketing release beyond the root commit message; the release conclusion is based on all locally reachable evidence.
- GitHub PR prose helps infer intent but code/tests/diffs are authoritative.
- Docker runtime services were not started during Phase 1; compose validation passed.

