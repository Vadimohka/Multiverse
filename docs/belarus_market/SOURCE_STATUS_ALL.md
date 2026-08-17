# Belarus Market — row-level status (websites only)

`VERIFIED` is reserved for a fixture-backed extraction regression plus
anonymous live smoke. Telegram rows are intentionally absent.

Readiness command: `python3 scripts/smoke_belarus_market_pack.py`. It returns
report rows only; no request is made until `--live` is passed, and neither
that report nor a `PASS` result changes `verification.json`, preset status,
or `Schedule.enabled`. Use `--source-key NEWS-08` (keys are case-insensitive)
to limit an anonymous-public live check to a source. Every market-news row
below remains `DRAFT` until an operator records the required evidence through
the normal reviewed promotion process.

| Key | Dataset | Status | Evidence / blocker |
| --- | --- | --- | --- |
| UL-01 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-02 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-03 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-04 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-05 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-06 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-07 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-08 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-09 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-10 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-11 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-12 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-13 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-14 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-15 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-16 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-17 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-18 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-19 | legal deposits | DRAFT | fixture + live extraction smoke required |
| UL-20 | legal deposits | VERIFIED | matrix fixture + anonymous HTTP smoke |
| UL-21 | legal deposits | DRAFT | fixture + live extraction smoke required |
| FL-01 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-02 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-03 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-04 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-05 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-06 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-07 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-08 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-09 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-10 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-11 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-12 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-13 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-14 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-15 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-16 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-17 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-18 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-19 | retail deposits | DRAFT | fixture + live extraction smoke required |
| FL-20 | retail deposits | DRAFT | fixture + live extraction smoke required |
| NEWS-01 | market news | DRAFT | list/detail fixtures retained; report-only live smoke required |
| NEWS-02 | market news | DRAFT | list/detail fixtures retained; report-only live smoke required |
| NEWS-04 | market news | DRAFT | NBRB anonymous read timeout; fixture + stable public transport required |
| NEWS-05 | market news | DRAFT | NBRB anonymous read timeout; fixture + stable public transport required |
| NEWS-06 | market news | DRAFT | listing fixture retained; live smoke and document MIME/parser proof required |
| NEWS-07 | market news | DRAFT | list/detail fixtures retained; report-only live smoke required |
| NEWS-08 | market news | DRAFT | listing/detail/page fixtures retained; report-only live smoke required |
| NEWS-09 | market news | DRAFT | public/paid fixtures retained; report-only live smoke required |
| NEWS-10 | market news | DRAFT | finance-scope fixture retained; report-only live smoke required |
| NEWS-11 | market news | DRAFT | scoped category fixture retained; report-only live smoke required |
| NEWS-12 | market news | DRAFT | scoped category fixture retained; report-only live smoke required |
| NEWS-13 | market news | DRAFT | scoped category fixture retained; report-only live smoke required |
| NEWS-14 | market news | DRAFT | topic fixture retained; report-only live smoke required |
| NEWS-15 | market news | DRAFT | gold/paywall fixture retained; report-only live smoke required |
| NEWS-16 | market news | DRAFT | page-distinctness fixtures retained; report-only live smoke required |
| NEWS-03 | market indicators | DRAFT | BCSE indicator series fixture + smoke required |
| indicator-nbrb-refinancing | market indicators | DRAFT | NBRB series fixture + stable transport required |
| indicator-nbrb-daily-rates | market indicators | DRAFT | NBRB series fixture + stable transport required |
| indicator-nbrb-precious-metals | market indicators | DRAFT | NBRB series fixture + stable transport required |
