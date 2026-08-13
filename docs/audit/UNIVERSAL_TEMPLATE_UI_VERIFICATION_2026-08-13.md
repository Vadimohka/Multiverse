# Universal workflow template — manual UI verification (2026-08-13)

## Scope and boundary

The checks below were performed through the local Parser Studio UI, using
**Create workflow** from a built-in template, selecting a public Source,
configuring the visible controls, and pressing **Test node**. They are live
observations dated 2026-08-13, not a permanent claim about third-party sites.

The system templates are source-neutral: they contain no literal URL, source
ID, dataset ID, domain selector or credential. They support only public
representations. CAPTCHA, login, paywall and access-control bypasses were not
tested and are not supported.

Verification project: `UI verification 2026-08-13`.
Verification dataset: `UI verification records`.

## Built-in templates and observed result

| Template | Workflow / source used in UI | UI evidence |
|---|---|---|
| Public HTML cards | Phoenix Refining blog | CSS cards `a.card`, link `:scope`, `h4`; Extract returned 12 real cards. |
| Public HTML table | NBRB daily rates | `table`, header row `0`; Extract returned real currency/rate rows. |
| Public JSON API / XHR | NBRB exchange-rate API | JSONPath `$[*]`; returned AUD, AMD and BRL records with provenance. |
| Public RSS / XML feed | NBRB RSS | item selector `item`; returned title/date/URL records with provenance. |
| Public list → detail pages | Phoenix Refining blog | URL pagination pages 1 and 2; one public detail page returned a title and non-empty article body. |
| Public browser: cards, states, detail | TexMetals news | Browser action clicked `button[aria-label="Go to next page"]`; result included `https://texmetals.com/news?page=2`; one detail page returned `h1` and non-empty `.Text-wrapper` body. Traverse reconciliation: discovered 5, succeeded 1, failed 0. |
| Public document: PDF, DOCX, XLSX, CSV | Public CSV `2014_usa_states.csv` | Direct document download retained a raw artifact and Extract returned 52 rows, including Alabama, Alaska and Arizona. |
| Public catalogue → documents | FSU public CSV directory | `a[href$=".csv"]`, detail maximum 2: Traverse discovered 33 file links and downloaded `addresses.csv` and `airtravel.csv`; Extract returned 17 rows with source URL, filename and raw-artifact provenance. |

## Detailed browser traversal configuration

The browser template was manually configured in the UI as follows:

- card CSS: `a[href^="/all-news/"]`
- detail link CSS: `:scope`
- next-page selector: `button[aria-label="Go to next page"]`
- maximum listing pages: `2`
- open detail: enabled; maximum detail pages: `1`
- detail fields: `title = h1`, `body_text = .Text-wrapper`

The UI node-test selected the browser traversal strategy, made two listing
requests, retained artifacts for pages 1 and 2, and retained a separate
artifact for the opened article. The returned record had non-empty `title`,
`body_text`, and `__provenance.url`.

## Document and catalogue configuration

For the direct document test, the public source URL was:

`https://raw.githubusercontent.com/plotly/datasets/master/2014_usa_states.csv`

For the catalogue test, the public index URL was:

`https://people.sc.fsu.edu/~jburkardt/data/csv/`

The catalogue workflow used visible Traverse controls only:

- detail link selector: `a[href$=".csv"]`
- maximum detail documents: `2`

The source and documents were public, required neither authentication nor a
CAPTCHA, and the UI result showed the returned MIME type, URL and artifact
reference. The generic template remains usable for PDF, DOCX, XLSX and CSV;
the live evidence here proves its document transport/fan-out path with CSV.

## Corrections made while verifying

- Browser Traverse reuses Acquire HTML only for static, non-paginated listings;
  a browser-controlled paginator now always opens a browser so its click is
  actually executed.
- Node-test preview excludes duplicated raw detail-page/document bodies while
  retaining record fields, page URLs, counts, errors and artifact references.
- The UI reverse proxy allows long-running public browser traversals instead of
  prematurely returning a 60-second gateway timeout.

## Reproducible checks

- `uv run pytest -q tests/unit/test_workflow_contracts.py -k browser_traverse`
- `uv run pytest -q tests/integration/test_api.py -k node_test`
- `uv run ruff check packages/workflow_engine/strategies.py apps/api/app/routers/workflows.py`
- `cd apps/frontend && npm run test -- --run && npm run build`
- `docker compose config --quiet`

The full regression suite is run separately before hand-off.
