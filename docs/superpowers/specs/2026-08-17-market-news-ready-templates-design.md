# Market News Ready Templates Design

## Goal

Deliver installable, declarative templates for every public website source in
the Belarus Market news passport. A fresh installation must create one
workflow per source and publish records into the shared `market-news` dataset
without site-specific code in the workflow engine.

## Scope

The implementation covers NEWS-01, NEWS-02 and NEWS-04 through NEWS-16.
NEWS-03 remains an intraday market-indicator workflow and is excluded from
`market-news`. The Central Depository issuer directory used by the portfolio
agent is a securities-reference source, not a news source; it remains outside
this change.

## Design

`PASSPORT_MARKET_NEWS.md` remains the human-readable source of business
scope. `presets/belarus-market/news/source-profiles.json` is the editable
machine configuration for transport, listing/detail selectors, pagination,
date traversal and deterministic selection rules. The existing pack installer
compiles that configuration into one immutable source preset, source,
workflow and schedule per passport entry, all bound to `market-news`.

Each template emits the existing `MarketNewsRecord` shape with source
identity, canonical URL, identity key, public detail or permitted preview,
timestamps, access status, selection decision and rule version. Consumers
check coverage and then read `market-news` by `source_published_at` interval.

## Readiness and safety

Every source begins as an installable `DRAFT` template and can be run manually
for operator review. A schedule stays disabled until it has a retained public
fixture, source-contract regression, passing opt-in anonymous-public live
smoke and a `VERIFIED` manifest entry with both evidence references. No
importer or smoke command promotes a source or enables a schedule.

Failed requests, invalid dates, incomplete detail traversal, paywalls and
repeated pages remain visible in provenance and assessment codes; they never
become empty successful output.

## Source contract coverage

The generic engine is unchanged. Fixtures prove exact section scope,
list-to-detail extraction, half-open `Europe/Minsk` intervals, safe pagination
and date-order stopping, direct-document MIME handling, paywall metadata-only
handling, and deterministic INCLUDE/EXCLUDE/AMBIGUOUS rules. Tests exercise
real generic nodes and do not assert selector strings or add hostname logic.

## Delivery sequence

1. Harden registry and installer coverage for every template.
2. Add fixtures for BCSE, NBRB, Ministry of Economy, Ministry of Finance and
   Central Depository.
3. Add fixtures for PrimePress, Myfin, Phoenix Refining, Business Times and
   TexMetals.
4. Add opt-in live smoke and readiness reporting.

Each batch uses red-green tests and only promotes an independently proven
source.

## Non-goals

- No automatic external crawling or schedule enablement during installation.
- No paywall bypass.
- No migration of the Central Depository issuer directory into `market-news`.
- No public Data API, workflow identifier or user-authored revision change.

## Acceptance criteria

- A clean installation creates all 15 website-news templates in `market-news`.
- Every source has a complete declarative profile and a disabled draft schedule
  until explicit verification.
- `VERIFIED` requires fixture and successful opt-in live-smoke evidence.
- Existing universal, pack and Data API regressions pass.
