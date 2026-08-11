# Multiverse Data API Contract

## Status and compatibility

Base path: `/api/v1`. The contract extends the existing records endpoint. A request without `view` keeps the current approved-record behavior. UUID and unique dataset slug are both valid as `{dataset_ref}`.

All timestamps are timezone-aware ISO-8601. Responses normalize UTC as `Z`. Invalid or naive filter timestamps return `422`.

## Common endpoint

```http
GET /api/v1/datasets/{dataset_ref}/records
Authorization: Bearer <JWT-or-scoped-token>
```

Parameters:

| Parameter | Values | Semantics |
| --- | --- | --- |
| `view` | `current`, `latest_run`, `run`, `history` | Projection; default `current` |
| `run_id` | UUID | Required for `view=run` |
| `time_basis` | `source_published_at`, `source_modified_at`, `fetched_at`, `observed_at` | Timestamp used by filters/order |
| `from` | aware ISO-8601 | Inclusive lower bound |
| `to` | aware ISO-8601 | Exclusive upper bound |
| `at` | aware ISO-8601 | Exact second as `[floor(second), +1 second)` |
| `limit` | 1…1000 | Default 100 |
| `cursor` | opaque | Stable keyset cursor |
| `sort` | `asc`, `desc` | Default `desc` |
| `include_pending` | boolean | Authorized UI/review view only; default false |

`at` is mutually exclusive with `from`/`to`. `run_id` is rejected for other views rather than silently ignored.

## Response envelope

```json
{
  "items": [
    {
      "record_id": "record-uuid",
      "record_version_id": "version-uuid",
      "natural_key": "n050820261",
      "data": {
        "news_id": "n050820261",
        "title": "Результаты торгов",
        "url": "https://www.bcse.by/press-center/news/n050820261"
      },
      "timestamps": {
        "source_published_at": "2026-08-10T12:34:56Z",
        "source_modified_at": null,
        "fetched_at": "2026-08-10T12:35:03.412987Z",
        "observed_at": "2026-08-10T12:35:03.518006Z"
      },
      "provenance": {
        "run_id": "run-uuid",
        "source_id": "source-uuid",
        "raw_document_id": "raw-uuid"
      },
      "review_status": "APPROVED",
      "confidence": 1.0
    }
  ],
  "pagination": {
    "limit": 100,
    "next_cursor": null
  },
  "meta": {
    "dataset_id": "dataset-uuid",
    "dataset_slug": "bcse-news",
    "view": "current",
    "run_id": null,
    "time_basis": "observed_at",
    "from": null,
    "to": null,
    "at": null
  }
}
```

## A — Current state

Latest approved current content for every active natural key:

```http
GET /api/v1/datasets/bcse-news/records?view=current
```

`timestamps` and provenance correspond to the most recent observation of the current approved version, not `Record.updated_at`.

## B — Latest successful parser run

```http
GET /api/v1/datasets/bcse-news/records?view=latest_run
```

Returns exactly the records observed in the latest successfully persisted run that wrote to this dataset, including records whose content did not change. `meta.run_id` is resolved by the server.

An empty successful run returns `items: []` and its `run_id`; it must not silently fall back to an older non-empty run.

## C — Specific run

```http
GET /api/v1/datasets/bcse-news/records?view=run&run_id=8c03e638-46a0-4ae7-a7bf-9b19c063679a
```

The run must belong to observations for the dataset. Unknown run or dataset returns `404`; a run with no observations for that dataset returns an empty page with that `run_id` only when run/dataset association is known.

## D — From exact source publication timestamp

```http
GET /api/v1/datasets/bcse-news/records?view=current&time_basis=source_published_at&from=2026-08-10T12:34:56Z
```

Records with `source_published_at >= from`. Records whose source publication time is unknown are excluded from this filtered result.

## E — From parser observation/fetch timestamp

Observation time:

```http
GET /api/v1/datasets/bcse-news/records?view=latest_run&time_basis=observed_at&from=2026-08-10T12:34:56Z
```

Fetch time:

```http
GET /api/v1/datasets/bcse-news/records?view=run&run_id=8c03e638-46a0-4ae7-a7bf-9b19c063679a&time_basis=fetched_at&from=2026-08-10T12:34:56Z
```

`observed_at` is when persistence fixed the observation; `fetched_at` is when transport obtained source bytes. Neither is replaced by entity `updated_at`.

## Exact second

```http
GET /api/v1/datasets/bcse-news/records?time_basis=source_published_at&at=2026-08-10T12:34:56Z
```

Server query:

```text
source_published_at >= 2026-08-10T12:34:56.000000Z
AND source_published_at < 2026-08-10T12:34:57.000000Z
```

Microsecond values are not lost.

## Range

```http
GET /api/v1/datasets/bcse-news/records?time_basis=observed_at&from=2026-08-10T12:34:56Z&to=2026-08-11T12:34:56Z
```

Ranges are half-open: `from <= timestamp < to`. `to <= from` returns `422`.

## History

```http
GET /api/v1/datasets/bcse-news/records?view=history&time_basis=observed_at&from=2026-08-01T00:00:00Z
```

Returns observations/content versions rather than only current records. Multiple items may share `record_id`. Each item identifies `record_version_id` and `run_id`.

The existing endpoint remains available during migration:

```http
GET /api/v1/records/{record_id}/history
```

It is extended with source/fetch/observation provenance but not removed.

## Cursor semantics

Cursor encodes the selected timestamp, deterministic id tie-breaker, view and filter fingerprint. A cursor cannot be reused with changed filters (`400 INVALID_CURSOR`). Sorting is keyset-based:

```text
(selected_timestamp DESC NULLS LAST, observation_id DESC)
```

For unfiltered current records without an observation, fallback ordering is `(record.created_at, record.id)` during migration only.

## БВФБ agent examples

The external agent computes the requested civil-time interval. Multiverse only
applies that explicit interval to source publication timestamps; it does not
interpret phrases such as "yesterday".

Create a revocable read-only credential once (the cleartext `token` is returned
only by this response):

```http
POST /api/v1/api-tokens
Authorization: Bearer <administrator-JWT>
Content-Type: application/json

{
  "name": "BCSE news agent",
  "scopes": ["datasets:read"],
  "dataset_ids": ["<bcse-dataset-uuid>"],
  "rate_limit_per_minute": 120
}
```

All current БВФБ news published during 10 August in `Europe/Minsk`. The `+`
in an offset must be URL-encoded as `%2B`:

```http
GET /api/v1/datasets/bcse-news/records?view=current&time_basis=source_published_at&from=2026-08-10T00:00:00%2B03:00&to=2026-08-11T00:00:00%2B03:00&sort=asc&limit=100
Authorization: Bearer mv_<service-token>
```

Continue:

```http
GET /api/v1/datasets/bcse-news/records?view=current&time_basis=source_published_at&from=2026-08-10T00:00:00%2B03:00&to=2026-08-11T00:00:00%2B03:00&sort=asc&limit=100&cursor=<opaque>
```

Only what the latest scheduled parser run saw:

```http
GET /api/v1/datasets/bcse-news/records?view=latest_run
```

## Errors

```json
{
  "detail": "Cursor does not match request filters",
  "error": {
    "code": "INVALID_CURSOR",
    "message": "Cursor does not match request filters",
    "details": {},
    "request_id": "..."
  }
}
```

| HTTP | Code | Meaning |
| --- | --- | --- |
| 400 | `INVALID_CURSOR`, `BAD_REQUEST` | Cursor is invalid/mismatched or parameters are incompatible |
| 401 | `AUTHENTICATION_REQUIRED` | Missing, expired, revoked or invalid credential |
| 403 | `FORBIDDEN` | Credential lacks dataset scope or review role |
| 404 | `NOT_FOUND` | Dataset/run/resource unavailable |
| 409 | `CONFLICT` | Unique resource conflict |
| 422 | `VALIDATION_ERROR` | Timestamp or request validation failed |
| 429 | `RATE_LIMITED` | API consumer limit exceeded |

## Authentication migration

Existing JWT bearer tokens continue to work. Scoped service tokens use the same Authorization header and are read-only. Token secrets are shown once, hashed at rest, revocable, restricted to dataset ids and rate-limited per UTC minute. A `429` response includes `Retry-After`. No token may invoke workflow, review or administrative endpoints unless a separate future scope explicitly permits it.

## OpenAPI requirements

OpenAPI enumerates views/time bases, mutual exclusions, aware datetime formats, response envelope, cursor behavior and error schemas. Examples above are included in endpoint documentation and tested against generated schema.
