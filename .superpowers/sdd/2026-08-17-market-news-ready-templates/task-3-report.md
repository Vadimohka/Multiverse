# Task 3 report — official market-news fixture evidence

## Outcome

Added retained, sanitized list/detail evidence for NEWS-01, NEWS-02, NEWS-07
and NEWS-08, plus NEWS-08 pages 1 and 2. NEWS-06 retains only the anonymously
observed Economy direct-PDF listing link. NEWS-04 and NEWS-05 direct HTML
retrieval timed out, so their files are DRAFT route recheck artifacts and are
deliberately not registered as accepted fixture evidence. The fixture runner now supports the generic
`run_news_fixture_from_files(source_key)` convention and hermetic page fixtures.

All seven verification entries remain `DRAFT`. Every `live_smoke.result` is
`PENDING_OPERATOR_SMOKE`; no fixture result is presented as an operator smoke
or `VERIFIED` source. The pack's schedule test still proves only the existing
VERIFIED legal source is enabled (`sum(schedule.enabled) == 1`), so these news
schedules remain disabled.

No workflow-engine source branch or hostname conditional was added. Selector
and URL-pattern corrections remain declarative in
`presets/belarus-market/news/source-profiles.json`.

## TDD evidence

### RED — missing retained fixtures

After adding the parameterized contract and before adding fixture files:

```text
pytest -q tests/unit/test_market_news_fixtures.py -k official
FFFFFFF
7 failed
```

Each parameter failed with the intended `FileNotFoundError`, beginning with:

```text
tests/fixtures/belarus-market/news/news-01-list.html
```

The equivalent missing paths for NEWS-02, 04, 05, 06, 07 and 08 were reported
in their respective parameter cases. This was a missing-evidence RED, not a
syntax, import or unrelated runner error.

### GREEN — official contracts

After adding the retained evidence and declarative corrections:

```text
pytest -q tests/unit/test_market_news_fixtures.py -k official
........
8 passed
```

For NEWS-01, NEWS-02, NEWS-07 and NEWS-08, the contracts cover non-empty
title/canonical URL/date/body and auditable selection rule/reason. NEWS-08 also
covers two-page traversal, zero-failure detail reconciliation, descending page
dates and non-title detail text. NEWS-04 and NEWS-05 instead prove DRAFT/no
accepted fixture references with concrete timeout reasons. NEWS-06 proves only
the observed direct-PDF link passes the compiled generic frontier. The NBRB
statistics allowlist is tested as a declarative rule without treating the
DRAFT HTML artifacts as source evidence.

### Required regression set

```text
pytest -q tests/unit/test_market_news_fixtures.py \
  tests/unit/test_belarus_market_pack.py \
  tests/unit/test_extended_nodes.py
..........................................
42 passed
```

The run emitted four existing dependency/model warnings: two Pydantic field
shadowing warnings, one Starlette/httpx deprecation warning, and the existing
XML-as-HTML warning from
`test_rss_link_tail_is_extracted_without_a_site_specific_rule`. There were no
failures or errors.

## Anonymous live retrieval and sanitization

Retrieval used anonymous GETs with a descriptive user agent and no cookie jar,
authentication, credentials or session reuse. No response headers, cookies,
tracking parameters, analytics elements, social-share links, or raw full pages
were retained.

| Source | Anonymous observation on 2026-08-17 | Retained evidence |
| --- | --- | --- |
| BCSE releases/news | `/press-center/releases` returned HTTP 200; public `/press_center/calendar` JSON returned release/news cards; both public detail pages and `/solo/calendar` JSON returned HTTP 200. | Minimal rendered card and detail excerpts. The profile now targets the observed release card/pretty URL rather than an unrendered `/solo/calendar` anchor. |
| NBRB press | Direct `/rss/?p=press`, `/news/press`, and the detail request timed out. A public search index exposed the official `https://www.nbrb.by/Press/22322` route and title. | Only a clearly marked DRAFT recheck artifact using that official URL. No body/date was invented, and no NEWS-04 `fixture_refs` were accepted. |
| NBRB statistics | Direct `/news/statistics` timed out. The official `https://api.nbrb.by/AvgIntRatesDyn` endpoint returned HTTP 200 public JSON (8,008 bytes). | Only DRAFT route/title recheck artifacts. No NEWS-05 `fixture_refs` are accepted, and the API response is not represented as HTML proof. |
| Ministry of Economy | Section returned HTTP 200 and exposed a direct Q1 PDF link. A research request also observed the URL returning `application/pdf`, but the hermetic fixture runner did not exercise that MIME/body. | Only the listing/direct-link fixture is accepted. No PDF parsing, MIME handling, document timestamp or attachment extraction is claimed. |
| Ministry of Finance | Listing and selected article returned HTTP 200. The observed card URL is under `/ru/public_debt/...`, not `/ru/news/`. | Minimal card/article/date excerpt; profile URL pattern corrected declaratively to accept scoped `/ru/` articles and `/upload/` documents. |
| Central Depository | Pages 1 and 2 and the selected detail returned HTTP 200. Page 1 began at 17.08.2026; page 2 began at 10.08.2026. | Minimal page/date/link fixtures plus full-detail excerpt and two-page traversal evidence. |

All retained prose is a short structural excerpt or concise paraphrase. The
fixtures contain no personal contact data, user data, cookies, credentials,
paywalled body, PDF author metadata, or tracking parameters. Functional
`PAGEN_1=2` is retained only in declarative pagination configuration, not as a
tracking parameter.

## Declarative configuration evidence

- NEWS-01 uses the observed rendered press-release container and pretty
  `/press-center/releases/` detail route.
- NEWS-04 accepts the official site's observed `Press` path casing while
  retaining the generic RSS selector.
- NEWS-07 accepts the observed scoped article and direct-upload path families.
- Common generic detail fields recognize `.document-content` and
  `.news-content`; no PDF-specific field or source branch was added.
- NEWS-08 pagination remains the declarative `PAGEN_1={{page}}` template.

The file runner uses only profile selectors, source passport URLs and generic
filename conventions. Its page transport maps supplied fixture page URLs
without checking a source key or hostname. `visited_pages` is derived from the
generic traversal checkpoint.

## Verification registry and readiness

Five sources have accepted retained fixture paths: NEWS-01, NEWS-02, NEWS-07,
NEWS-08, and NEWS-06's list-only direct-link evidence. NEWS-04 and NEWS-05
intentionally have no `fixture_refs`. All seven entries retain:

```json
{"status": "DRAFT", "live_smoke": {"result": "PENDING_OPERATOR_SMOKE"}}
```

NEWS-04 and NEWS-05 reasons specifically record the direct NBRB timeout and no
accepted live HTML fixture. NEWS-06 explicitly records that PDF MIME/parser
behavior was not fixture-tested. The other reasons distinguish anonymous
fixture capture from an operator-run installed workflow.

## Concerns and follow-up

1. NBRB direct website/feed availability is the material unresolved concern.
   NEWS-04 and NEWS-05 must not be promoted until an operator can run the
   official endpoints and reconcile their current structure with these
   representative contracts.
2. The NEWS-04 files are DRAFT recheck artifacts, not accepted source fixtures:
   the list is an HTML-normalized indexed link shape and `news-04-feed.xml`
   tests only the ordinary RSS text-link shape. Neither contains a fabricated
   publication body or date.
3. NEWS-08 intentionally uses the single required representative detail file
   for links discovered on both retained pages. This proves pagination and
   fetch reconciliation, but an operator smoke must still confirm distinct
   live detail bodies.
4. Fixture capture proves deterministic configuration behavior, not terms of
   use, redistribution rights, operational availability, or production
   readiness. Those remain operator responsibilities while status is DRAFT.

## Review fix — accepted evidence boundaries and uppercase Press frontier

Review found that NEWS-05's unavailable representative HTML and NEWS-06's
synthetic HTML document response were still registered too strongly, and that
uppercase `Press` support was asserted only after hard-coded RSS extraction.

### RED evidence

The evidence-boundary tests were added before changing the registry/profile:

```text
pytest -q tests/unit/test_market_news_fixtures.py \
  -k 'unavailable_nbrb_statistics or economy_fixture_proves_only or uppercase_press'
FF.
2 failed, 1 passed
```

NEWS-05 failed because `fixture_refs` still contained its list/detail HTML.
NEWS-06 failed because its accepted refs still included the synthetic detail
HTML; the same test would also have rejected the generic `attachment_url`
field after the ref assertion was corrected.

The uppercase route test was then mutation-checked by temporarily replacing
NEWS-04's declarative pattern with `/statistics/`:

```text
pytest -q tests/unit/test_market_news_fixtures.py -k uppercase_press
F
assert [] == ['https://www.nbrb.by/Press/22322']
```

This demonstrates that the covering test executes the compiled profile's real
generic `build_url_frontier` filtering rather than merely comparing a hard-coded
RSS extraction result.

### Fix

- Removed NEWS-05 `fixture_refs`; list/detail files are explicitly marked as
  DRAFT timeout/recheck artifacts and contain no invented body/date.
- Registered only `news-06-list.html`; the test extracts its observed PDF link
  with the compiled selector and admits it through the generic frontier.
  Removed the generic `attachment_url` detail field and replaced the synthetic
  detail file with a non-evidence marker.
- Added a compiled-frontier assertion for `https://www.nbrb.by/Press/22322`
  using NEWS-04's declarative `/[Pp]ress/` profile. DRAFT/no accepted body/date
  assertions remain intact.
- Replaced NEWS-05 fixture execution with a direct declarative selection-rule
  test, which does not promote the unavailable HTML.

### GREEN evidence

Focused review contracts:

```text
pytest -q tests/unit/test_market_news_fixtures.py \
  -k 'unavailable_nbrb_statistics or economy_fixture_proves_only or uppercase_press or source_specific_contracts or explicit_draft_reason'
11 passed
```

Required regression set after all review fixes:

```text
pytest -q tests/unit/test_market_news_fixtures.py \
  tests/unit/test_belarus_market_pack.py \
  tests/unit/test_extended_nodes.py
45 passed
```
