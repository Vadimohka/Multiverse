# Multiverse Target Architecture

## Product boundary

Multiverse is a self-hosted no-code extraction and versioned-data platform. Core owns **how** data is fetched, traversed, extracted, normalized, observed and served. A workflow/preset owns **what** a particular source means.

Site selectors, field names, date formats, URL patterns and source vocabulary are valid in an executable workflow or named preset. They are forbidden as branches or scoring bonuses in generic profiler/crawler/parser code.

## Architectural invariants

1. No hostname, CMS id, CSS class or business vocabulary affects generic control flow.
2. Generated workflow is an explicit, versioned configuration; runtime does not silently rewrite it.
3. Fetch, observation, publication and entity lifecycle timestamps have distinct fields.
4. A content version is created when content changes; an observation is created every time a record is seen in a successful run.
5. Every observation can point to its source, run, content version and raw evidence.
6. Empty output is an explicit terminal outcome, never an implicit success.
7. Any backend source/crawl/extraction capability required for normal use is available in the no-code editor.
8. Examples and site presets are isolated from core and carry explicit `scope`/`portable` metadata.

## System topology

```text
Source + Browser/API/Document profile
  → FetchEnvelope(raw artifact, final URL, fetched_at, response metadata)
  → optional traversal strategy(pages, tabs, detail links, API cursors)
  → configured extractor(records + reserved metadata)
  → transform → validation → mapping → configured deduplication
  → persistence
      ├── Record (current approved snapshot)
      ├── RecordVersion (immutable changed content)
      └── RecordObservation (record seen in a run)
  → review/current projection
  → Data API(current | latest successful run | run id | history)
```

## Configuration model

### Source

Source contains transport defaults only:

- entry/base URL;
- HTTP, browser, API or document adapter;
- headers, cookies/profile reference, locale/timezone;
- allowed domains and rate policy;
- profiler snapshot and workflow binding.

### Executable workflow

Workflow graph contains the full source-specific behavior:

- selector/XPath/JSONPath;
- repeat container and fields;
- list/detail merge rules;
- pagination/traversal;
- browser actions/tabs;
- transformations and validation;
- natural/deduplication keys;
- reserved timestamp extraction;
- output dataset/review/empty policy.

### Template scopes

- `SYSTEM_TEMPLATE`: reusable, no source/dataset binding, `portable=true`.
- `PROJECT_TEMPLATE`: reusable inside a project; may contain project-level mappings.
- `SITE_PRESET`: intentionally source-specific and labelled; may include literal URLs/selectors.
- `EXECUTABLE`: bound graph that is always allowed to contain site configuration.

Portability is validated from explicit metadata and graph schema, never by searching strings such as `bcse` or `press_center`.

## Generic Source Profiler

Profiler proposes configuration and confidence; it never infers business semantics from vocabulary.

### Repeating candidate score

For every sampled candidate group calculate:

- occurrence count;
- DOM depth and container span;
- structural similarity of child-tag paths;
- consistent child/attribute presence;
- text/link/image density;
- percentage of non-empty suggested fields;
- selector uniqueness within the parent scope;
- selector stability penalty for generated/dynamic identifiers;
- extractability: generated field selectors must work relative to every sampled container;
- nested-candidate penalty when parent carries a richer stable record.

Names default to structural `text`, `link`, `image`, `field_1…`. HTML semantics (`h1`, `time`, `itemprop`, OpenGraph, JSON-LD) may yield generic semantic names such as `title` or reserved timestamps. `rate`, `deposit`, `currency`, product and bank vocabulary never affects selection.

### Profiler output contract

```json
{
  "schema_version": 2,
  "transport": {"recommended": "HTTP", "confidence": 0.91},
  "repeating_candidates": [],
  "tables": [],
  "pagination_candidates": [],
  "detail_candidates": [],
  "metadata_extractors": [],
  "xhr_candidates": [],
  "warnings": []
}
```

Each proposal includes reasons and confidence so the UI can explain rather than silently decide.

## Fetch and traversal contracts

### FetchEnvelope

Every transport returns:

```json
{
  "requested_url": "...",
  "final_url": "...",
  "status_code": 200,
  "content_type": "text/html",
  "body": "...",
  "fetched_at": "2026-08-10T12:34:56.123456Z",
  "raw_document_id": "...",
  "headers": {},
  "artifacts": []
}
```

HTTP/browser/document/API adapters must preserve `fetched_at` and evidence identifiers through traversal and extraction.

### URL policy

Canonicalization is configurable:

- normalize scheme/host/default port/path;
- sort query parameters;
- drop configured tracking parameters only;
- preserve all other query parameters by default;
- resolve relative URLs against final URL;
- enforce allowed domains, maximum depth/pages and cycle keys.

### Traversal strategy

One generic traversal primitive accepts a listing loader, item URL extractor, optional paginator and detail extractor. It emits arbitrary configured records; it has no article schema. Supported pagination strategies are `next_link`, `query_template`, `offset`, `cursor`, `browser_action` and `tabs`. All are opt-in. Retry, timeout, delay, rate-limit and failure policy are explicit.

## Generic extraction and reserved metadata

Field extractor sources:

- CSS selector;
- XPath;
- JSONPath;
- attribute/text/HTML;
- regex;
- response/OpenGraph/JSON-LD/RSS metadata;
- constant or expression.

Reserved metadata maps are explicit:

```json
{
  "source_published_at": {
    "source": "css",
    "selector": "time.published",
    "attribute": "datetime",
    "format": "DD.MM.YYYY HH:mm:ss",
    "locale": "ru-BY",
    "timezone": "Europe/Minsk"
  }
}
```

Core also offers generic ordered metadata fallbacks for standardized signals: `<time datetime>`, `article:published_time`, JSON-LD `datePublished/dateModified`, RSS/Atom and configured JSON fields. No site hostname participates. Absent source date is `null`; parser observation/fetch timestamps always exist.

## Persistence redesign

### Record

Current approved projection identified by dataset + natural key. Keeps compatibility with current UI/export and points logically to current approved version.

### RecordVersion

Immutable content version:

- record and origin run;
- monotonic version number;
- normalized business payload and stable hash;
- review state/confidence;
- first observed timestamp;
- optional source publication/modification timestamps captured for that content.

Review does not change observation/fetch semantics.

### RecordObservation

One row for every record seen in a run, including unchanged content:

- `dataset_id`, `record_id`, `record_version_id`;
- `run_id`, `source_id`;
- optional `raw_document_id`;
- timezone-aware `fetched_at`, mandatory `observed_at`;
- optional `source_published_at`, `source_modified_at`;
- `natural_key`, `content_changed`, `created_at`;
- unique `(run_id, record_id)`.

This table answers latest-run and time-based queries without fabricating content versions.

### RawDocument

Raw artifact remains immutable. Fetch creates it before extraction where storage is enabled. An observation links to the most specific raw evidence available; additional evidence can remain a structured list in metadata until a many-to-many evidence table is justified.

## Timestamp rules

- Store aware UTC values (`TIMESTAMPTZ` equivalent; SQLite tests normalize at API boundary).
- Serialize ISO-8601 with `Z`.
- Naive user/source values require an explicit configured timezone.
- `source_published_at`: source-declared original publication.
- `source_modified_at`: source-declared modification.
- `fetched_at`: transport completed receiving the source.
- `observed_at`: persistence fixed this record observation.
- `created_at/updated_at`: database entity lifecycle only.
- run boundaries remain `started_at/finished_at`.
- Exact second `at=t` is `[t, t + 1 second)`.

## Data API

The additive endpoint is `GET /api/v1/datasets/{dataset_ref}/records`, where `dataset_ref` accepts UUID or unique slug. Default remains current approved data for compatibility.

Views:

- `view=current`;
- `view=latest_run`;
- `view=run&run_id=...`;
- `view=history`.

Time filters select `time_basis=source_published_at|source_modified_at|fetched_at|observed_at` with `from`, `to` or `at`. Ordering is stable `(selected_time DESC, observation/version/id DESC)` and cursor-based. Legacy `offset` remains temporarily supported for the current view.

Full request/response semantics are specified in `DATA_API_CONTRACT.md`.

## Authentication

Existing JWT roles remain for UI. Machine clients receive a separate revocable, hashed API token:

- bearer token shown only at creation;
- scopes such as `datasets:read` and optional dataset allow-list;
- no write/review/admin permissions;
- expiry, last-used timestamp and revocation;
- audit log records token identity.

The first Data API slice may ship with existing JWT compatibility; token storage/endpoints are a subsequent P2 security slice and must not weaken JWT.

## БВФБ site preset

БВФБ is a first-class `SITE_PRESET`, not a core branch. The preset includes:

- calendar listing URL and `sFrom/sTo` query templates;
- news URL selector/pattern;
- `#title`, `.dynamic-publicationdate`, `#pc_body` and attachment selectors;
- date format/locale/timezone mapping into `source_published_at`;
- natural key `news_id`;
- source label/language constants;
- incremental start-date parameter supplied by workflow/run config.

The generic crawler knows only configured query templates/selectors. The agent reads results using the standard Data API:

```http
GET /api/v1/datasets/bcse-news/records?view=current&time_basis=source_published_at&from=2026-08-10T00:00:00Z
```

## No-code UI

The editor must provide guided controls for:

- CSS/XPath/JSONPath and attribute/text/HTML;
- repeating container and sampled preview;
- detail link, URL resolution, allowed domains and merge mode;
- all pagination strategies including browser tabs/actions;
- reserved timestamp field, format, locale and timezone;
- generic field mapping/normalizers/validation;
- natural and dedupe keys;
- output dataset/review/empty policy;
- preview of FetchEnvelope/provenance.

Raw JSON remains an advanced escape hatch, not the primary UX.

## Failure semantics

- Transport/extraction errors have stable codes, retryability and evidence.
- `FAIL_FAST`, `CONTINUE` and partial-result thresholds are explicit.
- Empty output terminates as `SUCCESS_EMPTY_ALLOWED`, `SUCCESS_EMPTY_UNEXPECTED` or failure according to output policy.
- All terminal statuses terminate SSE.
- A run is `latest successful` only if its final status is in the documented success set and persistence committed.
- Partial crawl failures are reported in run metadata; they never silently disappear.

## Compatibility and migration

1. Add tables/columns without dropping existing data.
2. Backfill one observation for each existing `RecordVersion` using its run/observed timestamp when possible.
3. Preserve old records endpoint defaults and response aliases during transition.
4. Keep `published_at` in БВФБ payload temporarily, but canonical metadata/API field is `source_published_at`.
5. Replace string-based legacy blocking with explicit template metadata; preserve executable workflows.
6. Remove site-specific core code only after equivalent preset/config tests pass.

## Test architecture

Offline fixtures cover simple cards, list→detail, tables, next/query/tab pagination, JS page, JSON API, JSON-LD publication, missing publication, unusual classes and competing repeating containers. Generic tests never assert a real site name/class. Separate preset tests may assert БВФБ selectors and fixture snapshots.

Integration tests prove:

- unchanged records appear in the latest run through observations;
- current differs from latest run;
- exact-second and range filters are timezone-aware;
- source publication and parser observation filters differ;
- raw provenance survives persistence;
- cursor order is stable;
- empty terminal SSE closes;
- legacy current-record consumer remains compatible.

