# Multiverse Repair Plan

> Execution rule: add a failing generic or contract test before each behavior change. Site preset tests may use site selectors; core tests may not.

## Implemented in the restoration change

The P0 vertical slice and the P1 core-universality work in this plan are now
implemented: per-run observations/dataset runs, timestamped Data API views,
cursor pagination, scoped dataset tokens, the executable БВФБ preset, generic
detail field/date-range configuration, neutral profiler scoring, generic URL
identity/deduplication, honest blocked/empty run statuses, and matching no-code
controls. Remaining scale optimizations and broader fixture coverage stay
prioritized below rather than being represented as completed functionality.

## Delivery order

1. P0 БВФБ/Data API vertical slice.
2. P0 generic profiler and run terminal regressions.
3. P1 remove remaining site/business semantics from core.
4. P1 crawler/fetch/provenance contract hardening.
5. P2 no-code controls, M2M auth and documentation.
6. Full verification, merge into local `main`, then remove merged local branches/worktree.

## P0.1 — Per-run observation model

**Problem**  
Unchanged records are absent from `RecordVersion`, so latest-run membership and parser-time queries are impossible.

**Root cause**  
Content change and source observation share one model/event.

**Regression introduced by**  
Present in release baseline `f7b4f25`; not a later regression.

**Desired generic behaviour**  
Create one `RecordObservation` for every record seen in a successfully persisted run, linked to the reused or new `RecordVersion`, run/source/raw evidence and distinct timestamps.

**Files**  
`apps/api/app/models.py`, new Alembic migration, `apps/api/app/routers/workflows.py`, `apps/api/app/routers/data.py`, schemas.

**Tests**  
Integration: first run creates version+observation; unchanged second run creates no version but creates observation; changed third run creates both; rollback leaves neither partial state.

**Migration impact**  
Additive table/indexes; backfill observations from existing versions when `run_id` exists. No data drop.

**Risk**  
High: persistence/review transaction path. Mitigate with current-review compatibility tests.

## P0.2 — Stable Data API views and time filters

**Problem**  
Current endpoint lacks latest-run/run/history views, slug lookup, stable cursor and source/fetch/observation filters.

**Root cause**  
API exposes `Record` projection only and orders on `updated_at`.

**Regression introduced by**  
Missing since release.

**Desired generic behaviour**  
Implement `DATA_API_CONTRACT.md` additively; legacy request without `view` remains valid.

**Files**  
`apps/api/app/routers/data.py`, `apps/api/app/schemas.py`, error helpers/OpenAPI docs.

**Tests**  
Current vs latest successful run; explicit run; slug; source publication vs observed; exact second with microseconds; half-open range; invalid naive time; cursor stability; predictable 4xx.

**Migration impact**  
None beyond observation table. Old response top-level `limit/offset/total` retained during transition where offset is requested.

**Risk**  
High: consumer compatibility and SQL portability (PostgreSQL/SQLite).

## P0.3 — БВФБ news preset on generic crawler

**Problem**  
Required news chain exists but depends on hardcoded article functions and fixed `sFrom/sTo` behavior in core; canonical source timestamp is ambiguous.

**Root cause**  
A site workflow was promoted into the crawler implementation instead of remaining configuration.

**Regression introduced by**  
Article assumptions originate in `f7b4f25`; query/template extensions in `536b755`/`d7b2444`.

**Desired generic behaviour**  
Generic crawler accepts configured listing query templates, link extraction, detail fields and timestamp mapping. `bcse_news_graph()` is a labelled site preset with selectors, Minsk timezone and natural key. Agent can query news from `source_published_at >= from`.

**Files**  
`packages/workflow_engine/nodes.py`, `catalog.py`, `apps/api/app/seed_templates.py`, bootstrap/template router, tests and fixture.

**Tests**  
Offline БВФБ-shaped fixture verifies preset; separate neutral fixture proves the same crawler emits arbitrary fields; date parse produces aware UTC; missing date is null; query parameters are supplied only by config.

**Migration impact**  
Keep legacy payload `published_at` alias for a transition; metadata persists canonical `source_published_at`.

**Risk**  
High: live site HTML may change. Offline fixture is contract proof; optional live smoke remains non-blocking.

## P0.4 — Generic repeating-container regression

**Problem**  
Current profiler chooses a repeated anchor instead of the record container; current suite fails.

**Root cause**  
`d7b2444` prioritizes direct links without checking relative extractability.

**Regression introduced by**  
`d7b2444`.

**Desired generic behaviour**  
Rank structurally consistent containers with usable descendant fields; remove banking vocabulary/site fixtures from core test.

**Files**  
`apps/api/app/services/source_profiler.py`, `tests/unit/test_source_workflow.py`, new profiler fixtures.

**Tests**  
Neutral competing repeated groups, unusual class names, nested anchor/card, sampled population consistency.

**Migration impact**  
Existing saved profiles do not change automatically; re-profile produces improved suggestion.

**Risk**  
Medium: heuristic ranking. Return candidate reasons/confidence so users can override.

## P0.5 — Empty run terminal event status

**Problem**  
SSE polling may never close for successful empty runs.

**Root cause**  
Terminal-status set differs from `determine_run_status` output set.

**Regression introduced by**  
Empty status additions were not propagated in `0f6ba26`/later lifecycle code.

**Desired generic behaviour**  
One shared terminal/success status definition used by persistence, latest-run resolution and SSE.

**Files**  
`apps/api/app/enums.py` or shared constants, workflow/runs routers.

**Tests**  
Allowed/unexpected empty run closes events and latest-success selection has documented semantics.

**Migration impact**  
None.

**Risk**  
Low.

## P1.1 — Remove semantic dedupe/table inference from core

**Problem**  
Hardcoded bank fields and finance vocabulary affect generic outputs.

**Root cause**  
Missing configured header mappings and dedupe keys.

**Regression introduced by**  
`297856b`, `536b755`.

**Desired generic behaviour**  
Generic key sanitization plus explicit mapping/normalizer; dedupe uses configured fields or dataset natural keys.

**Files**  
`packages/workflow_engine/nodes.py`, `catalog.py`, presets/tests.

**Tests**  
Non-financial table and compound-key fixtures; bank behavior preserved only by an example config.

**Migration impact**  
Legacy presets gain explicit mappings before hardcode removal.

**Risk**  
Medium.

## P1.2 — Replace BCSE marker filtering with template metadata

**Problem**  
Generic API hides/blocks workflows by inspecting site strings and literal URLs.

**Root cause**  
No explicit template scope/portability/migration version.

**Regression introduced by**  
`d7b2444`.

**Desired generic behaviour**  
Executable source config is always permitted; portable templates are validated structurally and carry explicit scope.

**Files**  
`models.py`, migration, workflow/template schemas/routers, frontend template UI.

**Tests**  
Site-bound executable runs; portable template rejects unresolved bindings; no business regex in routers.

**Migration impact**  
Backfill existing built-ins/project templates. Preserve user workflows.

**Risk**  
Medium.

## P1.3 — URL, pagination and browser explicitness

**Problem**  
All query parameters are stripped; browser tabs are silently enabled; crawler does not forward its tab config.

**Root cause**  
URL identity and traversal defaults were designed around one source.

**Regression introduced by**  
`d7b2444`.

**Desired generic behaviour**  
Preserve query by default, drop configured tracking keys, sort query; all pagination/browser strategies opt-in and forwarded explicitly.

**Files**  
`packages/workflow_engine/nodes.py`, `catalog.py`, templates.

**Tests**  
Query-detail identities, query pagination, configured drop list, tab opt-in/off, cycle/max bounds.

**Migration impact**  
Preset explicitly enables tab behavior; URL natural keys may need one-time reconciliation documented, not automatic destructive rewrite.

**Risk**  
High for existing natural keys.

## P1.4 — Provenance envelope propagation

**Problem**  
Fetch time/raw artifact metadata is nested inconsistently and can disappear before persistence.

**Root cause**  
Node outputs have conventions but no typed envelope/reserved metadata contract.

**Regression introduced by**  
Present since release, amplified by multiple fetch nodes.

**Desired generic behaviour**  
All fetch nodes emit a common envelope; traversal copies provenance to records; transforms preserve reserved metadata; hashing excludes it.

**Files**  
`packages/workflow_engine/types.py`, fetch/extract nodes, workflow persistence.

**Tests**  
HTTP/browser/API/document provenance and hash stability.

**Migration impact**  
Accept legacy nested artifact formats during transition.

**Risk**  
High.

## P2.1 — Guided no-code controls

**Problem**  
Backend options are hidden or require raw JSON; timestamp extraction cannot be configured normally.

**Root cause**  
Node catalog and frontend editors lag runtime capabilities.

**Desired generic behaviour**  
Guided controls described in target architecture, with raw JSON as advanced mode.

**Files**  
`packages/workflow_engine/catalog.py`, `apps/frontend/src/workflow-editor.tsx`, selector picker API/UI.

**Tests**  
Frontend type/build/lint and catalog contract tests; UI-level configuration serialization tests where practical.

**Migration impact**  
Existing graph JSON remains readable.

**Risk**  
Medium.

## P2.2 — Scoped machine token

**Problem**  
Short-lived user JWT is impractical for an external news agent.

**Root cause**  
Authentication was designed for interactive UI only.

**Desired generic behaviour**  
Hashed, revocable, read-only `datasets:read` token, optional dataset allow-list and audit identity. JWT remains supported.

**Files**  
Models/migration/security/dependencies/new token router/OpenAPI/UI settings.

**Tests**  
Creation returns secret once; stored hash; allowed dataset read; denied write/other dataset; revoked/expired token.

**Migration impact**  
Additive.

**Risk**  
High security risk; implement after Data API correctness and review separately.

## P2.3 — Generic dataset UI and documentation

**Problem**  
Bank-preferred columns and stale docs make generic data hard to use; smoke depends on a removed demo.

**Desired generic behaviour**  
Columns derive from data/schema; docs match actual API/UI; smoke creates/finds its own deterministic fixture.

**Files**  
`apps/frontend/src/pages.tsx`, `scripts/smoke_test.py`, README/docs/OpenAPI.

**Tests**  
Frontend lint/build and smoke from clean database.

**Migration impact**  
None.

**Risk**  
Low.

## P3 — Further capability work

- Cursor/API rate limiting and token UI polish.
- Shared browser sessions/cookies and `Retry-After` support.
- Profiler pagination/XPath/JSON schema proposal quality.
- Crawl checkpoint/recovery and performance/load tests.
- Many-to-many raw evidence relation if one observation regularly needs multiple artifacts.

## Verification matrix

Run after each P0 slice and again at completion:

```bash
make test
make lint
make frontend-lint
make frontend-build
python scripts/smoke_test.py
docker compose config --quiet
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

Where Docker is available, run API/worker/database and a deterministic offline fixture workflow. Optional live БВФБ smoke is reported separately and never replaces offline tests.

## Repository consolidation

After all required checks pass:

1. Merge the repair branch into the existing local `main` without rewriting history.
2. Verify `main` contains `origin/main`, the historical local merge and the repair commits.
3. Remove only local branches fully merged into `main` using safe deletion.
4. Remove the now-unused worktree.
5. Confirm `git branch` lists only `main`, status is clean, refs/history remain reachable through `main`.

No reset, clean, forced deletion or history rewrite is permitted.
