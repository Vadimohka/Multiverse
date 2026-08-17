# Task 2 report — generic market-news fixture runner

## Delivered

- Added `tests/fixtures/belarus-market/news/__init__.py` with
  `run_news_fixture(source_key, listing_html, details, window)`.
- The helper derives the named source's existing preset with `_preset_config`,
  compiles it through `compile_preset`, and executes the real generic Acquire,
  v2 Traverse, Extract, Process, and Assure components.
- The in-memory `httpx.MockTransport` serves the requested source listing and
  only canonical URLs explicitly supplied in `details`; every other fixture
  request returns HTTP 404.
- Added fixture coverage for full public detail content/date/provenance, a
  valid empty window, partial detail failure, and repeated pagination.

No production workflow-engine code was changed, and no hostname/source
condition was added.

## RED evidence

Command:

```text
pytest -q tests/unit/test_market_news_fixtures.py -k fixture_runner
```

Before the helper was implemented, collection failed as expected with:

```text
ImportError: cannot import name 'run_news_fixture' from 'news'
```

The failing test was `test_fixture_runner_keeps_detail_date_and_provenance`.

## GREEN evidence

Focused test after implementation:

```text
pytest -q tests/unit/test_market_news_fixtures.py -k fixture_runner
4 passed
```

Required regression set:

```text
pytest -q tests/unit/test_market_news_fixtures.py tests/unit/test_extended_nodes.py tests/unit/test_universal_fixture_matrix.py
39 passed
```

The suite emitted existing third-party/model warnings only; there were no test
failures or errors.

## Test-only adapter rationale

The helper resolves `{{run.from}}`/`{{run.to}}` in the copied preset because
the individual generic node interfaces accept already-resolved config values;
that rendering normally happens in the workflow/run layer. It also sets a
fixture-page budget of one unless `window["max_pages"]` requests more, so a
single supplied listing fixture cannot silently trigger unsupplied pagination
URLs.

Native detail extraction normalizes source timestamps to UTC (`Z`). The helper
converts the returned record's `source_published_at` back to the profile's
declared source timezone solely for fixture assertions and presentation. The
underlying date-window validation still runs against the unmodified generic
node result.

The generic Traverse output carries per-detail errors and its `stop_reason`,
whereas Assure's status codes represent ratio validation rather than those
transport events. The helper therefore exposes the generic fixture evidence as
`DETAIL_FAILURE` and `REPEATED_PAGE` alongside the real Assure codes. This
translation is test-only and depends only on generic traversal state, never a
source key or hostname branch.

## Self-review

- The runner uses profile selectors, detail fields, transforms, and assurance
  settings from the compiled existing profile; it does not duplicate source
  extraction logic.
- A missing detail URL produces an actual mocked 404, and Assure receives the
  real traversal reconciliation, proving partial coverage rather than a mock
  call assertion.
- The repeated-page case uses the existing NEWS-05 next-link profile. Removing
  generic repeated-page protection would remove the exposed event and fail the
  test.
- The full-detail case verifies body content, source-local publication time,
  provenance state, and `PASS` assurance.
