# Complete Multiverse Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the approved Multiverse target architecture so generic workflows can configure every required extraction path and external clients can query large, versioned datasets reliably.

**Architecture:** Keep site knowledge in presets and fixtures. Add a SQL-backed record query service, a shared transport policy for HTTP/browser crawls, explicit resumable crawl output, guided UI editors, and operational API boundaries. Preserve existing workflow JSON and Data API response compatibility.

**Tech Stack:** Python 3.14, FastAPI, SQLAlchemy 2, Alembic, httpx, Playwright, BeautifulSoup/lxml, React 19, TypeScript, Vite, pytest, Ruff.

## Global Constraints

- Core code contains no hostname, organization, industry vocabulary, or site selectors.
- Existing workflow JSON and JWT consumers remain valid.
- Dataset API timestamps are timezone-aware UTC values and ranges are half-open.
- Every behavior change starts with a failing generic test.
- Offline fixtures replace live-site dependencies; the live БВФБ check is supplemental.
- No reset, clean, force-delete, force-push, or history rewrite.

---

### Task 1: SQL-backed Data API query service

**Files:**
- Create: `apps/api/app/services/data_records.py`
- Modify: `apps/api/app/routers/data.py:130-383`
- Test: `tests/integration/test_data_api_sql.py`

**Interfaces:**
- Consumes: `Dataset`, `Record`, `RecordVersion`, `RecordObservation`, `DatasetRun`, decoded cursor context.
- Produces: `query_dataset_records(db, request) -> DataRecordPage` containing joined rows, total, selected run, and next cursor values.

- [ ] **Step 1: Write the failing bounded-query test**

```python
def test_current_page_query_count_is_constant(client, auth, sql_counter, populated_dataset):
    response = client.get(
        f"/api/v1/datasets/{populated_dataset.slug}/records?view=current&limit=25",
        headers=auth,
    )
    assert response.status_code == 200
    assert len(response.json()["items"]) == 25
    assert sql_counter.count <= 6
```

- [ ] **Step 2: Run `pytest -q tests/integration/test_data_api_sql.py::test_current_page_query_count_is_constant` and verify it fails because query count grows with records.**

- [ ] **Step 3: Implement joined SQL selection** using a `row_number()` latest-observation subquery, current-version join, SQL time filters, `CASE` null rank, deterministic id tie-breaker, SQL `LIMIT + 1`, and a separate filtered count query.

```python
@dataclass(frozen=True)
class DataRecordPage:
    rows: list[tuple[Record, RecordVersion, RecordObservation | None]]
    total: int
    selected_run_id: str | None
    has_more: bool

def query_dataset_records(db: Session, request: DataRecordQuery) -> DataRecordPage: ...
```

- [ ] **Step 4: Add failing asc/desc NULLS LAST, current/latest_run/run/history, offset compatibility, and cursor context tests; implement SQL predicates for each and rerun the file.**

- [ ] **Step 5: Run `make test && make lint` and commit `perf: query dataset records with SQL keyset pagination`.**

### Task 2: Predictable API errors and scoped-token rate limits

**Files:**
- Create: `apps/api/app/errors.py`
- Create: `apps/api/app/services/rate_limit.py`
- Create: `migrations/versions/0011_api_rate_limits.py`
- Modify: `apps/api/app/main.py`, `models.py`, `schemas.py`, `routers/api_tokens.py`, `dependencies.py`
- Test: `tests/integration/test_api_operations.py`

**Interfaces:**
- Produces: error envelope `{error: {code, message, details, request_id}}` plus legacy `detail`; `enforce_api_token_rate_limit(db, token, now) -> None`.

- [ ] **Step 1: Write failing tests for validation, missing dataset, invalid cursor, and internal request errors returning the documented envelope with `X-Request-ID`.**

- [ ] **Step 2: Run the four tests and verify current `detail`-only responses fail them.**

- [ ] **Step 3: Register FastAPI handlers for `HTTPException` and `RequestValidationError`; preserve status codes and top-level `detail` during migration.**

- [ ] **Step 4: Write a failing test creating a token with `rate_limit_per_minute=2`, issuing three reads in one UTC minute, and expecting `429`, `Retry-After`, and `RATE_LIMITED`.**

- [ ] **Step 5: Add `ApiToken.rate_limit_per_minute` and `ApiUsageBucket(token_id, bucket_start, request_count)` with unique `(token_id, bucket_start)`; increment atomically and reject above the configured limit.**

- [ ] **Step 6: Test revoked, expired, unauthorized dataset, and rate-limited tokens; run migrations up/down/up; commit `feat: harden machine API errors and rate limits`.**

### Task 3: Shared HTTP transport policy

**Files:**
- Create: `packages/workflow_engine/transport.py`
- Modify: `packages/workflow_engine/nodes.py`, `catalog.py`, `types.py`
- Test: `tests/unit/test_transport_policy.py`

**Interfaces:**
- Produces: `FetchPolicy.from_config(config)`, `request_with_policy(client, method, url, policy, **kwargs) -> httpx.Response`, and serializable `FetchAttempt` diagnostics.

- [ ] **Step 1: Write a failing real-policy test where responses are `429 Retry-After: 2`, `503`, then `200`; assert delays `[2.0, 1.0]`, three attempts, and final response.**

- [ ] **Step 2: Verify failure because no shared policy exists.**

- [ ] **Step 3: Implement bounded retries, exponential backoff, `Retry-After` seconds/date parsing, retryable status configuration, timeout validation, and injectable async delay.**

- [ ] **Step 4: Add failing tests proving `HTTPRequestNode`, `DownloadFileNode`, `FollowLinksNode`, and `CrawlLinksNode` all use the same policy and return attempt diagnostics; wire them to it.**

- [ ] **Step 5: Add catalog fields `request_retries`, `retry_backoff_seconds`, `retry_statuses`, `respect_retry_after`, `cookies`, and `headers`; run unit tests and commit `feat: share configurable fetch retry policy`.**

### Task 4: Crawl safety, session continuity, and resume contract

**Files:**
- Modify: `packages/workflow_engine/nodes.py`, `types.py`, `catalog.py`
- Test: `tests/unit/test_crawl_resilience.py`

**Interfaces:**
- Produces: `CrawlResult.records`, `failures`, `completed_urls`, `resume_token`; consumes optional `resume_token` and configured allowed domains.

- [ ] **Step 1: Write failing tests for relative/canonical duplicates, disallowed domain, max pages, cycle, and explicit empty `allowed_domains` semantics.**

- [ ] **Step 2: Implement one canonical frontier helper used by follow-links and crawl-links, with same-origin default and explicit domain allow-list.**

- [ ] **Step 3: Write a failing session test where listing response sets a cookie and detail request succeeds only when the same `httpx.AsyncClient` carries it.**

- [ ] **Step 4: Refactor crawl listing/detail HTTP transport to share one client and configured cookies without exposing cookie values in diagnostics/artifacts.**

- [ ] **Step 5: Write a failing partial-failure test: two URLs succeed, one fails, returned `resume_token` contains only the failed canonical URL; a second execution with that token fetches only the failure.**

- [ ] **Step 6: Implement signed opaque resume payload scoped to workflow node/run input, `error_policy` values `FAIL`, `CONTINUE`, `REQUIRE_MINIMUM`, and stable failure diagnostics.**

- [ ] **Step 7: Run crawler tests and commit `feat: add safe resumable crawl sessions`.**

### Task 5: Complete generic offline fixture matrix

**Files:**
- Create: `tests/fixtures/universal/cards.html`, `list.html`, `detail-one.html`, `detail-two.html`, `table.html`, `next-1.html`, `next-2.html`, `query-pages.json`, `tabs.html`, `js-shell.html`, `json-api.json`, `jsonld-date.html`, `no-date.html`, `unusual-classes.html`, `competing-containers.html`
- Create: `tests/unit/test_universal_fixture_matrix.py`
- Modify: `source_profiler.py`, `nodes.py` only when a failing capability test requires it.

**Interfaces:**
- Exercises public node/profiler contracts; fixtures contain no bank, exchange, deposit, or production hostname vocabulary.

- [ ] **Step 1: Add literal expected-output tests for simple cards, list→detail, and HTML table; verify any missing behavior fails, then implement only the missing generic behavior.**

- [ ] **Step 2: Add next-link, query-parameter, and tab pagination tests with page/cycle bounds; verify red then green.**

- [ ] **Step 3: Add JS shell/XHR JSON and direct JSON API tests; profiler must propose browser/API modes without business vocabulary.**

- [ ] **Step 4: Add JSON-LD `datePublished`, missing publication date, unusual CSS classes, and competing-container tests; expected timestamp is aware UTC or null.**

- [ ] **Step 5: Search generic core for fixture/site vocabulary, run the complete matrix, and commit `test: cover universal extraction fixture matrix`.**

### Task 6: Profiler and browser-action completeness

**Files:**
- Modify: `apps/api/app/services/source_profiler.py`, `packages/workflow_engine/nodes.py`, `catalog.py`
- Test: `tests/unit/test_profiler_capabilities.py`, `tests/unit/test_browser_actions.py`

**Interfaces:**
- Profiler emits `pagination_candidates`, `metadata_candidates`, stable CSS and XPath alternatives, table/JSON schema hints; browser actions validate a generic discriminated shape.

- [ ] **Step 1: Write failing profiler tests for next links, query pagination, JSON-LD publication/modified fields, table columns, JSON arrays, and CSS selectors requiring escaping.**

- [ ] **Step 2: Implement structural suggestions with confidence/reasons and no domain vocabulary.**

- [ ] **Step 3: Write failing browser-action tests for click, fill, select, hover, press, wait, wait_for, scroll, and JavaScript; assert unknown/missing selector errors are predictable.**

- [ ] **Step 4: Remove the duplicate `wait_for` branch, validate actions before execution, and add browser storage-state/profile cookie support by configuration.**

- [ ] **Step 5: Run profiler/browser tests and commit `feat: complete profiler and browser action contracts`.**

### Task 7: Guided no-code editors and token management UI

**Files:**
- Create: `apps/frontend/src/node-editors.tsx`, `apps/frontend/src/api-tokens.tsx`
- Modify: `workflow-editor.tsx`, `pages.tsx`, `App.tsx`, `api.ts`, styles
- Test: `apps/frontend/src/node-editors.test.tsx`, `apps/frontend/src/api-tokens.test.tsx`

**Interfaces:**
- Produces graph-compatible editors for browser actions, pagination, retry/session, crawl constraints, timestamp extraction, mappings/normalizers, validation, natural keys, and output; token UI shows secret once and supports revoke.

- [ ] **Step 1: Add Vitest/Testing Library configuration and a failing serialization test for browser actions, pagination, detail fields, natural keys, and timestamp timezone/format.**

- [ ] **Step 2: Implement focused editors that update existing node `config` JSON without changing saved graph shape; keep raw JSON under Advanced.**

- [ ] **Step 3: Add a failing UI test creating a scoped dataset token, displaying the secret once, copying it, then revoking it.**

- [ ] **Step 4: Implement the API token page and navigation using `/api/v1/api-tokens`; never persist the clear token in local/session storage.**

- [ ] **Step 5: Add guided controls for all catalog fields that currently render as raw JSON, run frontend test/lint/build, and commit `feat: finish guided workflow and token UI`.**

### Task 8: Documentation, load verification, and release consolidation

**Files:**
- Modify: `README.md`, `docs/USER_GUIDE.md`, `docs/api.md`, `docs/audit/REPAIR_PLAN.md`, `docs/audit/DATA_API_CONTRACT.md`
- Create: `scripts/load_test_data_api.py`
- Test: full repository matrix.

**Interfaces:**
- Documents exact deployment, БВФБ first-run/schedule flow, token creation, error/rate contract, resume token, and migration path.

- [ ] **Step 1: Run a 10,000-record SQLite/PostgreSQL-compatible load fixture through `scripts/load_test_data_api.py`; assert bounded SQL count, stable pages, no duplicates, and report elapsed time without a brittle timing threshold.**

- [ ] **Step 2: Update documentation from verified behavior and mark every repair item implemented or explicitly non-goal with evidence.**

- [ ] **Step 3: Run `make test`, `make lint`, frontend test/lint/build, smoke, Alembic up/down/up, both compose configs, deterministic runtime fixture, and optional live БВФБ smoke.**

- [ ] **Step 4: Request independent code review, fix every Critical/Important finding with TDD, and repeat the full matrix.**

- [ ] **Step 5: Merge into local `main`, safely delete merged worktree/branch, confirm only local `main`, push through protected-branch PR, wait for CI, merge, and synchronize local `main`.**

## Self-review result

- Coverage: all remaining P2/P3 items in `docs/audit/REPAIR_PLAN.md` and every crawler/API/no-code requirement from the approved target architecture map to Tasks 1–8.
- Placeholder scan: no deferred implementation markers or unspecified test steps remain.
- Type consistency: query, transport, crawl-result, error, token, and UI configuration interfaces are named once and consumed consistently by later tasks.
