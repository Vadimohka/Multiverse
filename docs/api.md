# API

OpenAPI доступен по `/api/docs`, схема — `/api/v1/openapi.json`.

Machine-to-machine consumers should use the versioned Dataset Data API. It
supports current records, latest successful run, a specific run, history,
cursor pagination and timezone-aware filters by `source_published_at`,
`source_modified_at`, `fetched_at` or `observed_at`.

The normative contract, scoped-token creation, response examples and exact
second/range semantics are documented in
[audit/DATA_API_CONTRACT.md](audit/DATA_API_CONTRACT.md).
