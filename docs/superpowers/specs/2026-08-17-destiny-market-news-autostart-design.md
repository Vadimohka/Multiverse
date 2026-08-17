# Destiny market-news autostart design

## Goal

When the Docker Compose application is brought up for `destiny.by`, the
Belarusian market digest is immediately provisioned and starts collecting
without an operator configuring sources, workflows, or schedules. Telegram is
not part of the deployment.

## Scope

- Keep the existing 15 public website news workflows and the separate
  `market-indicators` workflow set; do not import Telegram sources.
- Make the market-pack installer create and maintain an hourly
  (`0 * * * *`, `Europe/Minsk`) enabled schedule for the `market-news` and
  `market-indicators` workflows only. Deposit templates retain their existing
  disabled schedules.
  The existing Celery Beat minute tick will claim and enqueue the first due
  hourly occurrence after services are up; no request handler performs a
  crawl synchronously.
- Make the setting explicit and idempotent: a digest package schedule is
  reconciled to the hourly default on every bootstrap. A user-created schedule
  and non-digest package schedule are not altered. Existing digest schedules
  are upgraded once, so old installs need no UI action.
- Keep startup import in `app.bootstrap.seed`; this already runs in FastAPI's
  lifespan after database setup. Docker Compose already starts API, worker
  queues and Celery Beat, so `docker compose up -d` is the only operational
  action.
- Document production environment values for the public domain:
  `PUBLIC_APP_URL=https://destiny.by` and `CORS_ORIGINS=https://destiny.by`.
  No DNS, TLS certificate, reverse-proxy, or public credentials are created by
  this repository change.
- Add a destination-specific API guide. It uses the existing scoped read token
  contract and `GET /api/v1/datasets/market-news/records`, filtering by
  `source_published_at` from a supplied timezone-aware date/time and paginating
  by cursor. It will include a `curl` command for `https://destiny.by`.

## Data flow

```text
docker compose up
  -> API lifespan: seed() -> idempotent market pack import
  -> package schedules: enabled hourly / Europe-Minsk
  -> Celery Beat minute tick -> durable schedule claim
  -> queue-specific worker -> workflow run -> market-news records
  -> Destiny Data API (Bearer scoped token) -> digest consumer
```

`market-indicators` remains a separate typed dataset. A downstream digest can
combine its result with `market-news`, but API consumers do not receive rates
as if they were article records.

## Safety and compatibility

- The installer continues to preserve source/workflow IDs, workflow revisions,
  dataset memberships and a non-package schedule chosen by an operator.
- The user now explicitly requests auto-start, so the former operator-only
  disabled setting is superseded only for package-owned schedules. A source
  failure becomes a run failure/retry with evidence; it does not prevent the
  rest of the hourly source runs.
- No generic workflow-engine, router, schema, data filtering or authentication
  behavior changes. The data API remains token-protected.

## Tests

1. Fresh pack installation creates enabled schedules with hourly cron and
   `Europe/Minsk` for news and indicators, while deposits remain disabled.
2. Reinstallation upgrades legacy digest schedules to those values while
   preserving IDs and does not alter a separately named operator schedule.
3. Bootstrap test proves the pack remains installed automatically.
4. API integration test proves `market-news` accepts `from` with
   `time_basis=source_published_at` and documents the exact public URL without
   changing the existing endpoint.
5. Existing fixture, installer, smoke and data API regressions remain green.
