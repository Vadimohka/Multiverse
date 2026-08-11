# API

OpenAPI доступен по `/api/docs`, схема — `/api/v1/openapi.json`.

Machine-to-machine consumers should use the versioned Dataset Data API. It
supports current records, latest successful run, a specific run, history,
cursor pagination and timezone-aware filters by `source_published_at`,
`source_modified_at`, `fetched_at` or `observed_at`.

The normative contract, scoped-token creation, response examples and exact
second/range semantics are documented in
[audit/DATA_API_CONTRACT.md](audit/DATA_API_CONTRACT.md).

Operational guarantees:

- filtering, ordering and cursor pagination run in SQL, not in application memory;
- machine errors contain `error.code`, `error.message`, `error.request_id` and
  the legacy top-level `detail` during migration;
- scoped tokens can read only configured datasets and return `429 Retry-After`
  when their per-minute limit is exceeded;
- `from` is inclusive, `to` is exclusive and `at` represents one exact second.
