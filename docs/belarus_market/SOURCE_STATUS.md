# Belarus Market source status

All entries below retain their passport URL. Only `UL-20` is `VERIFIED`: it
has a scoped matrix fixture and an anonymous HTTP smoke recorded on 14.08.2026.
Every other row remains `DRAFT` until it has the same evidence. This is an
explicit guard against false `VERIFIED` claims.

| Source set | Expected sources | Status | Blocking evidence |
| --- | ---: | --- | --- |
| Legal deposits | 21 (`UL-01`–`UL-21`) | 1 VERIFIED, 20 DRAFT | `UL-20` has a matrix fixture + HTTP smoke; each remaining source requires product/document fixture and smoke. |
| Retail deposits | 20 (`FL-01`–`FL-20`) | DRAFT | Each requires a retained product/detail fixture and a public transport smoke. |
| Website news | 15 (`NEWS-01`, `NEWS-02`, `NEWS-04`–`NEWS-16`) | DRAFT | All have editable list/detail transport, date-window and selection-rule profiles; each still requires fixture, reconciliation and extraction smoke. |
| Market indicators | 4 (BCSE + 3 NBRB URLs) | DRAFT | Each requires a retained series fixture and a public transport smoke. |

The passport documents remain the canonical row-level registry, including
canonical URLs, role/authority and source-specific route corrections. A
future import must create one immutable SourcePresetRevision and one workflow
per row, with `fixture_refs` populated before verification.

## Anonymous HTTP profile — 2026-08-14

The profile used the same public egress guard as the engine, followed no login
or CAPTCHA path, and intentionally did not change any source status.

| Group | Public HTTP 200 | Restricted/transport failure | Result |
| --- | ---: | ---: | --- |
| Legal deposits | 16/21 | 1×403, 4×timeout/protocol/egress | DRAFT |
| Retail deposits | 15/20 | 1×403, 4×timeout/protocol/egress | DRAFT |
| Website news | 13/15 | 2×NBRB read timeout (`NEWS-04`, `NEWS-05`) | DRAFT |

HTTP 200 only proves a reachable anonymous representation. It is not a
fixture regression or extraction live smoke and therefore cannot justify
`VERIFIED`. `passport_sources()` in
Telegram sources are intentionally out of scope and are not imported.
`app.services.belarus_market_pack` is the machine-readable 60-row matrix
used by the importer and its test.

## News live profile matrix — 2026-08-14

All checks used an ordinary anonymous HTTP request.  No login, CAPTCHA flow,
private API or paywall bypass was attempted.  The two NBRB timeouts remain
`DRAFT` rather than being silently switched to browser automation.

| Keys | Result | Preset status | Exact remaining proof |
| --- | --- | --- | --- |
| NEWS-01, NEWS-02 | HTTP 200 public HTML | DRAFT | public API/listing fixture + detail/topic reconciliation |
| NEWS-04, NEWS-05 | anonymous HTTP read timeout | DRAFT | stable public transport evidence, then list/detail fixture |
| NEWS-06 | HTTP 200 public HTML | DRAFT | HTML/file MIME fixture and publication-to-attachment linkage |
| NEWS-07 | HTTP 200 public HTML | DRAFT | dated listing/detail fixture |
| NEWS-08 | HTTP 200 public HTML, numbered `PAGEN_1` pages | DRAFT | pagination/date-order fixture and detail reconciliation |
| NEWS-09 | HTTP 200 public HTML, `PAGEN_1` pages | DRAFT | public-vs-paid fixture |
| NEWS-10 | HTTP 200 public HTML | DRAFT | finance/bank-context selection fixture |
| NEWS-11–NEWS-13 | HTTP 200 public HTML | DRAFT | scoped category-feed fixture (without global recommendations) |
| NEWS-14 | HTTP 200 public HTML | DRAFT | page pagination + precious-metals topic fixture |
| NEWS-15 | HTTP 200 public HTML | DRAFT | gold relevance and paywall-metadata fixture |
| NEWS-16 | HTTP 200 public HTML | DRAFT | page-distinctness and date-boundary fixture |

## Verified evidence

| Key | Source | Fixture | Live smoke | Status |
| --- | --- | --- | --- | --- |
| UL-20 | https://www.tcbank.by/business/deposits/ | `tests/fixtures/belarus-market/legal/ul-20-tc-bank.html` | 2026-08-14, anonymous HTTP: legal-entity rate matrix present | VERIFIED |
