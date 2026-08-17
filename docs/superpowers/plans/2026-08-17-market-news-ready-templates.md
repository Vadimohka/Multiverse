# Market News Ready Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver fixture-proven, installable templates for all 15 Belarus Market website-news sources without making the generic engine site-specific.

**Architecture:** `source-profiles.json` remains the single editable source for selectors, pagination and business rules. The existing installer compiles it into source presets, workflows and memberships in `market-news`; retained public fixtures test generic traversal, extraction, processing and assurance. A source stays `DRAFT` and its schedule disabled until fixture and opt-in live-smoke evidence are explicitly recorded.

**Tech Stack:** Python, pytest, FastAPI/SQLAlchemy, workflow-engine nodes, JSON profiles and public HTML/RSS/document fixtures.

**Spec:** `docs/superpowers/specs/2026-08-17-market-news-ready-templates-design.md`

## Global Constraints

- Keep hostnames, selectors and selection rules in `presets/belarus-market/news/source-profiles.json`; do not modify `packages/workflow_engine/` for a named site.
- Keep NEWS-03 in `market-indicators`; NEWS-01/02 and NEWS-04–16 are the 15 `market-news` sources.
- `VERIFIED` requires a fixture reference and recorded successful `--live` smoke. Installation and smoke reporting never enable a schedule.
- Preserve source keys, workflow IDs, user revisions and the public Data API.

---

### Task 1: Stabilise workflow binding and profile completeness

**Files:**
- Modify: `tests/integration/test_data_api_contract.py:638-652`
- Modify: `tests/unit/test_belarus_market_pack.py:66-84`
- Modify: `apps/api/app/services/belarus_market_pack.py` only if the red test proves incorrect source binding.

**Interfaces:** Consumes `passport_sources()`, `_preset_config()` and `install_belarus_market_pack()`. Produces name-independent source-to-workflow assertions.

- [ ] **Step 1: Write the failing regression assertion**

```python
source = next(item for item in sources if (item.get("settings") or {}).get("source_key") == "news-01")
workflow = next(item for item in workflows if item["graph_json"]["settings"].get("source_id") == source["id"])
assert workflow["graph_json"]["settings"]["dataset_id"] == dataset["id"]
```

- [ ] **Step 2: Verify red state**

Run: `pytest -q tests/integration/test_data_api_contract.py::test_market_news_bootstrap_binds_bcse_releases_to_the_shared_news_dataset`

Expected: the legacy `news-01:` display-name lookup fails after the `new-news-01` migration.

- [ ] **Step 3: Add the complete source-set test**

```python
expected = {"news-01", "news-02", "news-04", "news-05", "news-06", "news-07", "news-08", "news-09", "news-10", "news-11", "news-12", "news-13", "news-14", "news-15", "news-16"}
profiles = {source.key: _preset_config(source) for source in passport_sources() if source.key in expected}
assert set(profiles) == expected
assert all(config["bindings"]["dataset"] == "market-news" for config in profiles.values())
```

- [ ] **Step 4: Run green verification and commit**

Run: `pytest -q tests/unit/test_belarus_market_pack.py tests/integration/test_data_api_contract.py`

```bash
git add tests/integration/test_data_api_contract.py tests/unit/test_belarus_market_pack.py
git commit -m "test: bind market news workflows by source identity"
```

### Task 2: Create a generic fixture runner for news profiles

**Files:**
- Create: `tests/fixtures/belarus-market/news/__init__.py`
- Create: `tests/unit/test_market_news_fixtures.py`
- Modify: `tests/unit/test_extended_nodes.py` only if generic traversal lacks a required behavior.

**Interfaces:** `run_news_fixture(source_key, listing_html, details, window)` consumes an existing profile and fixture transport; it returns records plus generic assurance evidence.

- [ ] **Step 1: Write the failing full-detail fixture test**

```python
async def test_fixture_runner_keeps_detail_date_and_provenance():
    result = await run_news_fixture("news-08", LISTING, {DETAIL_URL: DETAIL}, WINDOW)
    assert result.records[0]["body_text"] == "Полный публичный текст"
    assert result.records[0]["source_published_at"] == "2026-08-14T09:00:00+03:00"
    assert result.assessment_status == "PASS"
```

- [ ] **Step 2: Verify red state**

Run: `pytest -q tests/unit/test_market_news_fixtures.py -k fixture_runner`

Expected: FAIL because `run_news_fixture` is absent.

- [ ] **Step 3: Implement the test helper through generic nodes**

```python
async def run_news_fixture(source_key, listing_html, details, window):
    source = {item.key: item for item in passport_sources()}[source_key]
    config = _preset_config(source)
    # Use generic crawl, detail extraction, transforms and assure config.
    # Fixture transport resolves only URLs supplied in details.
```

- [ ] **Step 4: Add generic safety examples**

```python
assert valid_empty.assessment_codes == ["EMPTY_VALID_WINDOW"]
assert partial.assessment_status == "PARTIAL"
assert "DETAIL_FAILURE" in partial.assessment_codes
assert "REPEATED_PAGE" in repeated_page.assessment_codes
```

- [ ] **Step 5: Run green verification and commit**

Run: `pytest -q tests/unit/test_market_news_fixtures.py tests/unit/test_extended_nodes.py tests/unit/test_universal_fixture_matrix.py`

```bash
git add tests/fixtures/belarus-market/news tests/unit/test_market_news_fixtures.py tests/unit/test_extended_nodes.py
git commit -m "test: add fixture runner for market news profiles"
```

### Task 3: Prove official-source configurations

**Files:**
- Create: `tests/fixtures/belarus-market/news/news-{01,02,04,05,06,07,08}-{list,detail}.html`
- Create: `tests/fixtures/belarus-market/news/news-04-feed.xml`
- Create: `tests/fixtures/belarus-market/news/news-08-page-{1,2}.html`
- Modify: `tests/unit/test_market_news_fixtures.py`
- Modify: `presets/belarus-market/verification.json`

**Interfaces:** Consumes the runner from Task 2. Produces public retained fixture evidence for BCSE, NBRB, the two Ministries and Central Depository.

- [ ] **Step 1: Write the failing parameterized contract**

```python
@pytest.mark.parametrize("source_key", ["news-01", "news-02", "news-04", "news-05", "news-06", "news-07", "news-08"])
async def test_official_fixture_has_full_detail_date_and_selection(source_key):
    result = await run_news_fixture_from_files(source_key)
    assert all(record["title"] and record["canonical_url"] for record in result.records)
    assert all(record["selection_rule_id"] and record["selection_reason"] for record in result.records)
```

- [ ] **Step 2: Verify red state**

Run: `pytest -q tests/unit/test_market_news_fixtures.py -k official`

Expected: FAIL with missing fixture files.

- [ ] **Step 3: Capture and sanitise source evidence**

Retain public list and detail examples without cookies, tracking data, personal data or credentials. Include NBRB RSS, an Economy direct-document link, and Central Depository pages 1 and 2 to prove descending dates and detail reconciliation.

- [ ] **Step 4: Assert the source-specific contracts**

```python
assert news_05.records[0]["selection_rule_id"] in {"nbrb-statistics-credit-deposit-v2", "nbrb-statistics-corporate-securities-v1"}
assert news_06.records[0]["attachment_url"]
assert news_08.pagination.visited_pages == 2
assert news_08.records[0]["body_text"] != news_08.records[0]["title"]
```

- [ ] **Step 5: Record fixtures but retain DRAFT status**

```json
"news-08": {"status": "DRAFT", "fixture_refs": ["tests/fixtures/belarus-market/news/news-08-page-1.html"], "live_smoke": {"result": "PENDING_OPERATOR_SMOKE"}}
```

- [ ] **Step 6: Run green verification and commit**

Run: `pytest -q tests/unit/test_market_news_fixtures.py tests/unit/test_belarus_market_pack.py tests/unit/test_extended_nodes.py`

```bash
git add tests/fixtures/belarus-market/news tests/unit/test_market_news_fixtures.py presets/belarus-market/verification.json
git commit -m "test: prove official market news templates"
```

### Task 4: Prove commercial and topic-scoped configurations

**Files:**
- Create: `tests/fixtures/belarus-market/news/news-{09,10,11,12,13,14,15}-list.html`
- Create: `tests/fixtures/belarus-market/news/news-09-detail.html`
- Create: `tests/fixtures/belarus-market/news/news-16-page-{1,2}.html`
- Modify: `tests/unit/test_market_news_fixtures.py`
- Modify: `presets/belarus-market/verification.json`

**Interfaces:** Consumes Task 2 runner and profiles NEWS-09 through NEWS-16. Produces deterministic paywall, topic and duplicate-page contracts.

- [ ] **Step 1: Write failing paywall and rule tests**

```python
async def test_paid_article_keeps_only_public_metadata():
    paid = next(record for record in (await run_news_fixture_from_files("news-09")).records if record["access_status"] == "PAYWALLED")
    assert paid["title"] and paid["canonical_url"]
    assert paid["body_text"] is None

async def test_finance_scope_keeps_decisions_explainable():
    records = (await run_news_fixture_from_files("news-10")).records
    assert {record["candidate_status"] for record in records} >= {"INCLUDE", "EXCLUDE", "AMBIGUOUS"}
```

- [ ] **Step 2: Verify red state**

Run: `pytest -q tests/unit/test_market_news_fixtures.py -k 'paid or finance_scope or texmetals'`

Expected: FAIL because source fixtures do not exist.

- [ ] **Step 3: Add sanitised representative fixtures**

Include public and paid PrimePress cards, bank-only and market-context Finance cards, scoped Myfin cards, precious-metal include/exclude examples, and two non-repeating TexMetals pages.

- [ ] **Step 4: Assert deterministic outcomes**

```python
assert finance_by_title["Bank deposit promotion"]["candidate_status"] == "EXCLUDE"
assert finance_by_title["Government bonds market"]["candidate_status"] == "INCLUDE"
assert business_times_paid["access_status"] == "PAYWALLED"
assert len(texmetals.records) == len({record["identity_key"] for record in texmetals.records})
```

- [ ] **Step 5: Run green verification and commit**

Run: `pytest -q tests/unit/test_market_news_fixtures.py tests/unit/test_belarus_market_pack.py`

```bash
git add tests/fixtures/belarus-market/news tests/unit/test_market_news_fixtures.py presets/belarus-market/verification.json
git commit -m "test: prove scoped market news templates"
```

### Task 5: Add report-only readiness smoke and handoff

**Files:**
- Modify: `apps/api/app/services/belarus_market_smoke.py`
- Modify: `scripts/smoke_belarus_market_pack.py`
- Modify: `tests/unit/test_belarus_market_smoke.py`
- Modify: `presets/belarus-market/README.md`
- Modify: `docs/belarus_market/SOURCE_STATUS.md`
- Modify: `docs/belarus_market/SOURCE_STATUS_ALL.md`

**Interfaces:** `run_smoke(args)` accepts `--live` and `--source-key`; it produces readiness rows but never writes `verification.json` or `Schedule.enabled`.

- [ ] **Step 1: Write failing explicit-live tests**

```python
def test_smoke_requires_live_flag_before_network(monkeypatch):
    monkeypatch.setattr("httpx.AsyncClient.get", fail_if_called)
    assert all(item["result"] == "SKIPPED_REQUIRES_LIVE" for item in run_smoke([]))

def test_live_smoke_never_promotes_source():
    assert run_smoke(["--live", "--source-key", "news-08"])[0]["status_after"] == "DRAFT"
```

- [ ] **Step 2: Verify red state**

Run: `pytest -q tests/unit/test_belarus_market_smoke.py`

Expected: FAIL because the report lacks live-gating or promotion-safety behavior.

- [ ] **Step 3: Implement report-only smoke and document manual promotion**

```python
def run_smoke(args):
    # Network is rejected unless --live is present.
    # Requests are anonymous and public-only.
    # Return source key, fixture, transport, result and reason; do not mutate state.
```

- [ ] **Step 4: Publish one readiness row per source**

```markdown
| Key | Dataset | Fixture | Smoke | Status | Reason |
| --- | --- | --- | --- | --- | --- |
| NEWS-08 | market-news | news-08-page-1.html | pending | DRAFT | operator live smoke required |
```

- [ ] **Step 5: Run final regression gates and commit**

Run: `pytest -q tests/unit/test_belarus_market_smoke.py tests/unit/test_belarus_market_pack.py tests/integration/test_data_api_contract.py && pytest -q && git diff --check`

```bash
git add apps/api/app/services/belarus_market_smoke.py scripts/smoke_belarus_market_pack.py tests/unit/test_belarus_market_smoke.py presets/belarus-market/README.md docs/belarus_market
git commit -m "feat: report market news template readiness"
```
