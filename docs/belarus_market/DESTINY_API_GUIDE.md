# Destiny.by: market-news API

The application provisions the `market-news` dataset and its hourly collectors
when the Compose stack starts. Telegram is not used. The Data API is protected:
create a scoped read-only token once, then use that token from the digest or
another service.

## 1. Create a read token

Sign in with the administrator configured in `DEFAULT_ADMIN_EMAIL` and
`DEFAULT_ADMIN_PASSWORD` on the production server. The clear-text service
token is returned only once; store it in the consumer's secret storage.

```bash
ADMIN_JWT=$(curl --silent --request POST 'https://destiny.by/api/v1/auth/login' \
  --header 'Content-Type: application/json' \
  --data '{"email":"admin@example.by","password":"replace-me"}' | jq -r '.access_token')

DATASET_ID=$(curl --silent 'https://destiny.by/api/v1/datasets' \
  --header "Authorization: Bearer $ADMIN_JWT" |
  jq -r '.[] | select(.slug == "market-news") | .id')

curl --request POST 'https://destiny.by/api/v1/api-tokens' \
  --header "Authorization: Bearer $ADMIN_JWT" \
  --header 'Content-Type: application/json' \
  --data "{\"name\":\"Destiny digest reader\",\"scopes\":[\"datasets:read\"],\"dataset_ids\":[\"$DATASET_ID\"],\"rate_limit_per_minute\":120}"
```

Save the returned `token` as `DESTINY_API_TOKEN`. Do not use the administrator
JWT for routine news retrieval.

## 2. Get news for one date

The example below returns all current news whose original publication time is
on 17 August 2026 in Minsk time. `from` is inclusive and `to` is exclusive;
the `+03:00` offset is encoded safely by `--data-urlencode`.

```bash
curl --get 'https://destiny.by/api/v1/datasets/market-news/records' \
  --header "Authorization: Bearer $DESTINY_API_TOKEN" \
  --data-urlencode 'view=current' \
  --data-urlencode 'time_basis=source_published_at' \
  --data-urlencode 'from=2026-08-17T00:00:00+03:00' \
  --data-urlencode 'to=2026-08-18T00:00:00+03:00' \
  --data-urlencode 'sort=asc' \
  --data-urlencode 'limit=100'
```

The response is an envelope with `items`, `pagination` and `meta`. Each
record carries normalized news in `items[].data`, the source publication time
in `items[].timestamps.source_published_at`, and source/run provenance.
Records without a known original publication time are excluded when
`time_basis=source_published_at` is filtered.

## 3. Continue a long result set

When `pagination.next_cursor` is non-null, request the next page using the
same filters and the opaque cursor exactly as received:

```bash
curl --get 'https://destiny.by/api/v1/datasets/market-news/records' \
  --header "Authorization: Bearer $DESTINY_API_TOKEN" \
  --data-urlencode 'view=current' \
  --data-urlencode 'time_basis=source_published_at' \
  --data-urlencode 'from=2026-08-17T00:00:00+03:00' \
  --data-urlencode 'to=2026-08-18T00:00:00+03:00' \
  --data-urlencode 'sort=asc' \
  --data-urlencode 'limit=100' \
  --data-urlencode "cursor=$NEXT_CURSOR"
```

Changing date filters, sort order or time basis invalidates a cursor. Market
rates and REPO values are a distinct typed dataset; retrieve them separately
from `/api/v1/datasets/market-indicators/records` rather than treating them as
news articles.
