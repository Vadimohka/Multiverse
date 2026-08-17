# Belarus Market source pack

This pack is a library of editable SourcePresetRevision defaults. It never
adds bank/site logic to the workflow engine: users can clone or create a
revision in **Пресеты источников**, then configure the compiled visual
workflow for any public website. See `docs/belarus_market/OPERATIONS.md`.

This folder is the review boundary for the Belarus Market Data Pack. Presets
must remain declarative and compile onto the existing v2 blueprint; no engine
node may branch on a bank, hostname or media outlet.

The currently retained passport URLs are listed in `SOURCE_STATUS.md`. A
preset may move from `DRAFT` to `VERIFIED` only after it has a retained
fixture and an anonymous-public live smoke using the chosen transport.

Datasets:

- `deposit-offers-legal`
- `deposit-offers-retail`
- `market-news`
- `market-indicators`

The schemas in `schemas/` are shared contracts; a source adds only its URL,
scoped extraction selectors, state coverage and mapping configuration.

## Readiness smoke and manual promotion

Use the report-only smoke command to see the current fixture and live-smoke
handoff state:

```bash
python3 scripts/smoke_belarus_market_pack.py
```

Without `--live`, the command makes no network requests and returns
`SKIPPED_REQUIRES_LIVE` rows. To profile one public source anonymously, an
operator must opt in explicitly:

```bash
python3 scripts/smoke_belarus_market_pack.py --live --source-key news-08
```

The command has no database or registry writes: it never changes
`verification.json`, source/preset status, or `Schedule.enabled`. A `PASS`
row is only public-representation evidence. Promotion remains a separate
manual review: retain and test the required fixture evidence, record the
operator's successful anonymous live smoke in `verification.json`, create a
reviewed immutable preset revision with `VERIFIED` status, and enable a
schedule only through the normal Schedule UI after that review.
