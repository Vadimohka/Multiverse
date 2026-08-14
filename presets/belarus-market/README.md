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
