# Belarus Market Passport Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a configurable Belarus market pack which lets a user maintain source settings in immutable preset/workflow revisions and reliably collect the passport-defined public website data through the Data API.

**Architecture:** Keep parsing-engine code generic. A `SourcePresetRevision` carries URL, allowed domains, transport order, selectors/JSONPath, state matrix, document rules, mapping, assertions, schedule, and fixture references; a user creates a new revision to change any of them. The pack importer validates these declarative files, creates datasets, sources, preset revisions, workflows and schedules, while fixture and live-smoke suites determine whether a revision may become `VERIFIED`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy/Alembic, Pydantic, pytest, workflow-engine Contract v2, JSON Schema and declarative JSON/YAML preset files.

**Spec:** `EPIC_MULTIVERSE_BELARUS_MARKET_DATA.md`, `PASSPORT_UL_DEPOSITS.md`, `PASSPORT_FL_DEPOSITS.md`, `PASSPORT_MARKET_NEWS.md`, plus the uploaded Excel passports.

## Global Constraints

- Use only anonymous-public website content; never automate login, sessions, CAPTCHA, paywall bypass, private APIs or Telegram parsing.
- A bank/source name, hostname, CSS selector, JSONPath, URL, topic rule, schedule and expected state belong in preset/workflow data, never in generic engine conditionals.
- Keep the existing seven-phase Contract v2 and immutable source-preset revision model.
- A preset can be `VERIFIED` only with retained fixture references and a passing opt-in live smoke for its chosen transport.
- Treat the three NBRB indicator URLs as `market-indicators`, not deposit offers; keep token marketplaces outside this EPIC unless a separate `token-offers` scope is approved.
- Preserve raw values, normalized values, provenance and record/version history; a changed rate must create a new version while an unchanged run must not.

---

## Files and responsibility map

- `packages/workflow_engine/nodes.py` — generic transformations, effective revision resolution and assertions.
- `packages/workflow_engine/strategies.py` — generic public transport, traversal and bounded-date pagination behavior.
- `apps/api/app/models.py`, `migrations/versions/0017_*.py` — schedule/source-revision metadata only if existing models cannot represent it.
- `apps/api/app/services/belarus_market_pack.py` — strict declarative-pack loading, validation, immutable import, workflow/schedule instantiation.
- `apps/frontend/src/preset-studio.tsx`, `apps/frontend/src/App.tsx` — no-code source-preset creation, revision and instantiation path.
- `presets/belarus-market/{legal,retail,news,indicators,policies,fixtures}/` — user-editable per-source configuration and retained public fixtures.
- `apps/api/app/routers/data.py` — coverage/evidence contract fixes discovered by E2E tests.
- `scripts/import_belarus_market_pack.py`, `scripts/smoke_belarus_market_pack.py` — reproducible install and opt-in anonymous live smoke.
- `tests/unit/`, `tests/integration/`, `tests/fixtures/belarus-market/` — generic, pack, fixture, API and E2E regression suites.
- `docs/belarus_market/SOURCE_STATUS.md` — one precise row per source and verification evidence.

## Task 0: Expose the source-preset contract through the no-code product

**Files:**
- Create: `apps/frontend/src/preset-studio.tsx`
- Modify: `apps/frontend/src/App.tsx`
- Modify: `apps/frontend/src/components.tsx`
- Test: `apps/frontend/src/preset-studio.test.tsx`

**Produces:** a user-facing route where a user selects a project and blueprint, creates a draft revision through guided fields, adds a URL/source policy/dataset/schema/fixture references, compiles and instantiates it into the existing visual workflow editor. Advanced JSON is an escape hatch, not the only configuration method.

- [x] Write a failing frontend test that creates a draft preset from its guided fields and verifies that the request has `kind=SourcePreset`, anonymous-public policy, URL, selected blueprint, `DRAFT` status and no site-specific application code.
- [x] Run `npm test -- preset-studio.test.tsx`; it failed because the page/route did not exist.
- [x] Implement the page with project and blueprint selectors, form fields for source URL, transport order, source role, dataset/schema reference, fixture links and status; prevent selecting `VERIFIED` without at least one fixture.
- [x] Add list/compile/instantiate controls and route the instantiated workflow to the existing visual editor, where selectors, mappings, state matrix, pagination, documents, assurances and schedules remain editable.
- [x] Add the route to navigation, run the frontend test, frontend build/lint and backend regression suite. Commit remains deferred until the full EPIC checkpoint.

## Task 1: Define the editable preset contract and pack manifest

**Files:**
- Create: `presets/belarus-market/manifest.json`
- Create: `presets/belarus-market/policies/public-anonymous-only.json`
- Modify: `apps/api/app/services/belarus_market_pack.py`
- Test: `tests/unit/test_belarus_market_pack.py`

**Produces:** `load_pack_manifest(root: Path) -> list[PresetDescriptor]`, where each descriptor names its schema, dataset, source role, file, revision status and schedule policy.

- [ ] Write failing tests asserting that every descriptor has a unique key, existing config file, valid dataset/schema reference, public-only policy and a status from `DRAFT`, `BLOCKED` or `VERIFIED`.
- [ ] Run `pytest tests/unit/test_belarus_market_pack.py -v`; expect failure because the manifest loader does not exist.
- [ ] Implement the typed manifest loader and reject missing/duplicate source keys before creating database rows.
- [ ] Replace Markdown-regex discovery in `passport_sources()` with manifest descriptors; keep passport Markdown/Excel as human source evidence, not as executable configuration.
- [ ] Run the focused test and `python3 -m pytest`; commit as `feat: validate declarative Belarus pack manifest`.

## Task 2: Complete common schemas and stable natural keys

**Files:**
- Modify: `presets/belarus-market/schemas/bank-deposit-offer-v2.json`
- Modify: `presets/belarus-market/schemas/market-news-v1.json`
- Modify: `presets/belarus-market/schemas/market-indicator-v1.json`
- Modify: `apps/api/app/services/belarus_market_pack.py`
- Test: `tests/unit/test_belarus_market_schemas.py`

**Produces:** schemas with all passport fields and dataset natural keys independent of a current rate/body summary.

- [ ] Write failing schema tests for the five retail fields (`revocability`, `replenishment_allowed`, `partial_withdrawal_allowed`, `capitalization`, `interest_payment_frequency`), deposit effective dates/evidence references, news external IDs and indicator series dimensions.
- [ ] Run `pytest tests/unit/test_belarus_market_schemas.py -v`; expect missing-property failures.
- [ ] Add nullable optional fields and keep only truly publishable identity fields required; define keys as deposit identity + segment + variant + currency + term/amount tier, news `source_id + external_id` with canonical-URL fallback, and indicator series + effective time + dimensions.
- [ ] Add import-time JSON-schema validation and tests that a rate/body change does not alter a natural key.
- [ ] Run focused tests and the full suite; commit as `feat: complete market data schemas`.

## Task 3: Finish and prove generic processing/assurance capabilities

**Files:**
- Modify: `packages/workflow_engine/nodes.py`
- Modify: `packages/workflow_engine/strategies.py`
- Test: `tests/unit/test_extended_nodes.py`
- Test: `tests/unit/test_workflow_contracts.py`
- Create: `tests/fixtures/generic/{matrix,tiers,revisions,date-boundary}/`

**Produces:** configuration-driven matrix expansion, current-document selection, date-bounded traversal and assurance results usable by any source.

- [ ] Add failing generic fixtures/tests for repeated/merged headers, amount and term tiers, archive/current ambiguity, documents lacking dates, descending and ascending pagination, and count drift.
- [ ] Run the corresponding focused tests and confirm each fails for the intended missing behavior.
- [ ] Implement only generic config keys such as `dimensionColumns`, `candidateDates`, `archivePatterns`, `expectedStates`, `countDrift`, `dateBoundary` and `dateWindow`; do not add site names or hostnames.
- [ ] Ensure date traversal stops before requesting older pages only after declared and fixture-proven order; otherwise it filters safely without early stop.
- [ ] Run unit suite; commit as `feat: harden generic market workflow assertions`.

## Task 4: Add per-source configuration for the 21 legal-deposit websites

**Files:**
- Create: `presets/belarus-market/legal/ul-01.json` through `ul-21.json`
- Create: `presets/belarus-market/fixtures/legal/`
- Modify: `presets/belarus-market/manifest.json`
- Test: `tests/fixtures/belarus-market/test_legal_presets.py`

**Produces:** one revision-ready configuration per passport source, each with canonical route, public transport order, scoped extraction/mapping/assertions and fixture reference.

- [ ] For each source, record the passport-correct canonical route; retain corrected BelVEB, Neo, RRB and Technobank routes.
- [ ] Capture anonymous public fixtures for Wave 1 first: TC Bank matrix, MTBank detail, then a current/archive document source; remove headers/cookies/secrets before storing.
- [ ] Write fixture tests asserting legal segment, expected product scope, extraction coverage, evidence preservation and natural-key stability.
- [ ] Add each successful source config/fixture to the manifest with status `VERIFIED` only after its live smoke; keep inaccessible/undetermined rows `DRAFT` or `BLOCKED` with exact reason.
- [ ] Repeat in small source batches, running `pytest tests/fixtures/belarus-market/test_legal_presets.py -v` after each batch; commit one coherent source batch at a time.

## Task 5: Add per-source configuration for the 20 retail-deposit websites and MyFin reconciliation

**Files:**
- Create: `presets/belarus-market/retail/fl-01.json` through `fl-20.json`
- Create: `presets/belarus-market/fixtures/retail/`
- Modify: `presets/belarus-market/manifest.json`
- Test: `tests/fixtures/belarus-market/test_retail_presets.py`

**Produces:** retail presets with explicit role assertions and all retail-condition mappings; MyFin is secondary reconciliation only.

- [ ] Write failing fixture tests for the complete retail field set and for rejecting corporate/global cards in an individual-deposit workflow.
- [ ] Configure each official bank source’s scoped list/detail/document strategy, tabs only where they reveal distinct data, and `sourceRole=INDIVIDUAL`.
- [ ] Configure `fl-20` as `SECONDARY`, producing reconciliation observations/issues and never overwriting an official bank record.
- [ ] Capture public RRB detail fixture as the rich-field anchor and fixtures for filters/current revisions; run a fixture regression after every source batch.
- [ ] Mark only smoke-proven configs `VERIFIED`; record all other concrete blockers; run full tests and commit batches.

## Task 6: Add the four NBRB/BCSE indicator presets

**Files:**
- Create: `presets/belarus-market/indicators/bcse-rates-repo.json`
- Create: `presets/belarus-market/indicators/nbrb-refinancing-rate.json`
- Create: `presets/belarus-market/indicators/nbrb-daily-rates.json`
- Create: `presets/belarus-market/indicators/nbrb-banking-precious-metals.json`
- Create: `presets/belarus-market/fixtures/indicators/`
- Modify: `presets/belarus-market/manifest.json`
- Test: `tests/fixtures/belarus-market/test_indicator_presets.py`

**Produces:** separate `market-indicators` coverage for all three uploaded-Excel NBRB URLs plus BCSE rates/REPO.

- [ ] Write failing fixture tests that prohibit deposit/news datasets from receiving numerical indicators.
- [ ] Configure date/effective-time parsing, indicator codes, units/currencies, source URLs and immutable-series natural keys for each public source.
- [ ] Capture a public fixture and live smoke each URL; otherwise register `BLOCKED` with its access reason instead of silently omitting it.
- [ ] Run indicator tests and full suite; commit as `feat: add NBRB and BCSE market indicators`.

## Task 7: Add the 16 website news workflows with declarative selection rules

**Files:**
- Create: `presets/belarus-market/news/news-01.json` through `news-16.json`
- Create: `presets/belarus-market/fixtures/news/`
- Modify: `presets/belarus-market/manifest.json`
- Test: `tests/fixtures/belarus-market/test_news_presets.py`

**Produces:** public-site list/detail workflows that return factual, attributable candidates for the external AI; Telegram does not appear in the manifest.

- [ ] Write failing tests for half-open Minsk time windows, Monday Friday-through-Sunday window, pagination/repeated-page handling, detail reconciliation, paywall classification, `AMBIGUOUS` records and a valid empty checked window.
- [ ] Configure per-source listing/detail/API/RSS transport, exact section scope and include/exclude/context rules from the passport; include full-detail topic selection for BCSE and NBRB statistics.
- [ ] Store source URL, canonical URL, published time, title, body/summary, external ID, access status, rule version and deterministic selection reason in each record/evidence envelope.
- [ ] Capture fixture coverage for NBRB press and Central Depository pagination first, then complete remaining sites in batches; execute fixture tests after each batch.
- [ ] Run full tests; commit as `feat: add public website news presets`.

## Task 8: Instantiate workflows, schedules and reproducible installation

**Files:**
- Modify: `apps/api/app/services/belarus_market_pack.py`
- Modify: `scripts/import_belarus_market_pack.py`
- Create: `scripts/smoke_belarus_market_pack.py`
- Test: `tests/integration/test_belarus_market_install.py`

**Produces:** idempotent installation of sources, immutable revisions, datasets, workflows and schedules from the manifest.

- [ ] Write failing integration tests that install the pack twice and expect no new revision for unchanged config, one new revision for a changed config, one workflow per descriptor and schedules only for active public presets.
- [ ] Add schedules: deposits weekly Monday in `Europe/Minsk`, news weekdays before 09:00 with explicit runtime window inputs, and on-demand workflows; use existing schedule model/API instead of a parallel scheduler.
- [ ] Make schedule and workflow settings user-editable through the existing revision/UI/API path; the pack is a seed, not an immutable hardcoded policy.
- [ ] Add a CLI smoke command requiring `--live`, executing only anonymous public requests and reporting source key, transport, role, count/valid-empty and failure reason without changing `VERIFIED` automatically.
- [ ] Run integration suite and full suite; commit as `feat: install Belarus workflows and schedules declaratively`.

## Task 9: Complete coverage and evidence API behavior

**Files:**
- Modify: `apps/api/app/routers/data.py`
- Modify: `apps/api/app/routers/workflows.py`
- Test: `tests/integration/test_data_api_contract.py`
- Test: `tests/integration/test_belarus_market_e2e.py`

**Produces:** coverage-first API results and opt-in evidence that connect a value to raw text, location, artifact, run and preset revision.

- [ ] Write failing API tests for missing/failed/partial/passed sources, `EMPTY_VALID_WINDOW`, schedule-window coverage, required versus secondary sources, compact records without evidence and `include=evidence` records with field evidence.
- [ ] Ensure the API returns the precise source preset revision ID, workflow ID, run ID, observation timestamp and assessment reason for every expected source.
- [ ] Redact request headers, cookies, credentials and private URLs from evidence serialization; test redaction explicitly.
- [ ] Add four E2E fixtures: TC Bank matrix → API, rich retail detail → API, current/archive document choice → API, paginated news window → coverage → API.
- [ ] Run the integration suite and `python3 -m pytest`; commit as `feat: complete coverage-first market data API`.

## Task 10: Verify source statuses and hand over operating documentation

**Files:**
- Modify: `docs/belarus_market/SOURCE_STATUS.md`
- Modify: `presets/belarus-market/README.md`
- Create: `docs/belarus_market/OPERATIONS.md`
- Test: `tests/unit/test_belarus_market_pack.py`

**Produces:** auditable readiness evidence and instructions for a user to revise presets/workflows safely.

- [ ] Write a test that status documentation and manifest contain exactly the same non-Telegram source keys.
- [ ] Generate a row per source with canonical URL, dataset, source role, declared transport, fixture path, live-smoke date/result, status and exact block reason; do not replace this table with aggregate counts.
- [ ] Document how a user clones a preset/workflow revision, changes a selector/URL/state/schedule, attaches a fixture, runs fixture regression and live smoke, then requests `VERIFIED` status.
- [ ] Document coverage-first API calls for legal deposits, retail deposits, indicators and news; state that external AI owns summaries, personalization, review routing and delivery.
- [ ] Run final full tests plus `python3 scripts/smoke_belarus_market_pack.py --live` for only opted-in sources; commit as `docs: complete Belarus market operating runbook`.

## Acceptance checklist

- [ ] 21 legal, 20 retail, 16 website-news workflows and 4 indicator workflows are present or explicitly `BLOCKED` with concrete evidence.
- [ ] Telegram is not parsed; login/CAPTCHA/paywall bypass is absent.
- [ ] Every source-specific value is editable declarative config, not a generic-engine conditional.
- [ ] A user can create, revise, compile, instantiate, test and schedule a preset through the no-code UI/API without editing repository files.
- [ ] Every `VERIFIED` row has retained fixture and passing live smoke; every other row is `DRAFT`/`BLOCKED` with a reason.
- [ ] Weekly deposit and weekday news schedules exist and pass install-idempotency tests.
- [ ] Coverage and opt-in evidence API contracts pass with scoped read-only authentication.
- [ ] Fixture, E2E and full regression suites pass.
