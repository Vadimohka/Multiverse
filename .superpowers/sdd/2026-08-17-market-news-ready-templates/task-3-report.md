# Task 3 report — official market-news fixture evidence

## Outcome

Added retained, sanitized list/detail evidence for NEWS-01, NEWS-02,
NEWS-05, NEWS-06, NEWS-07 and NEWS-08, plus NEWS-08 pages 1 and 2. NEWS-04's
direct feed/detail retrieval timed out, so its files retain only the indexed
official URL and concrete unavailability marker; they are deliberately not
registered as accepted fixture evidence. The fixture runner now supports the generic
`run_news_fixture_from_files(source_key)` convention and hermetic page fixtures.

All seven verification entries remain `DRAFT`. Every `live_smoke.result` is
`PENDING_OPERATOR_SMOKE`; no fixture result is presented as an operator smoke
or `VERIFIED` source. The pack's schedule test still proves only the existing
VERIFIED legal source is enabled (`sum(schedule.enabled) == 1`), so these news
schedules remain disabled.

No workflow-engine source branch or hostname conditional was added. Selector,
URL-pattern and attachment extraction corrections remain declarative in
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

For the six retrievable configurations, the contracts cover non-empty
title/canonical URL/date/body, auditable selection rule and reason, the exact
NBRB statistics allowlist decision,
Economy attachment URL, NEWS-08 two-page traversal and zero-failure detail
reconciliation, descending page dates, and non-title detail text. NEWS-04's
parameter instead proves it remains DRAFT with no accepted fixture references
and a concrete timeout reason. The separate RSS-shape check proves only that
the generic extractor consumes the retained indexed official URL contract.

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
| NBRB statistics | Direct `/news/statistics` timed out. The official `https://api.nbrb.by/AvgIntRatesDyn` endpoint returned HTTP 200 public JSON (8,008 bytes). | Minimal known official series/list/detail/XLSX shape. Verification explicitly requires an operator page recheck. |
| Ministry of Economy | Section returned HTTP 200. The direct Q1 PDF returned HTTP 200, `application/pdf`, 121,760 bytes. | Direct PDF link, short document text, and PDF creation timestamp. The PDF's personal author metadata was deliberately discarded. |
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
  `.news-content`; a generic document-link selector exposes `attachment_url`.
- NEWS-08 pagination remains the declarative `PAGEN_1={{page}}` template.

The file runner uses only profile selectors, source passport URLs and generic
filename conventions. Its page transport maps supplied fixture page URLs
without checking a source key or hostname. `visited_pages` is derived from the
generic traversal checkpoint.

## Verification registry and readiness

The six sources with accepted retained fixtures record their paths. NEWS-04
intentionally has no `fixture_refs`. All seven entries retain:

```json
{"status": "DRAFT", "live_smoke": {"result": "PENDING_OPERATOR_SMOKE"}}
```

NEWS-04 and NEWS-05 reasons specifically record the direct NBRB timeout;
NEWS-04 also records `no_live_fixture_accepted`. The other reasons distinguish
anonymous fixture capture from an operator-run installed workflow.

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
