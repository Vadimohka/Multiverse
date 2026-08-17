# Destiny Market News Autostart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the installed Belarusian market digest collect hourly without UI setup and document retrieval from `destiny.by` by source-publication date.

**Architecture:** The existing FastAPI lifespan already invokes the idempotent market-pack installer, while Docker Compose already runs Celery Beat and the queue workers. The installer becomes the only bootstrap change: package-owned schedules are enabled with `0 * * * *` and `Europe/Minsk`; record retrieval continues through the unchanged protected Data API.

**Tech Stack:** FastAPI, SQLAlchemy, Celery Beat, pytest, Docker Compose, Markdown.

**Spec:** `docs/superpowers/specs/2026-08-17-destiny-market-news-autostart-design.md`

## Global Constraints

- Import and execute no Telegram sources.
- Keep all source-specific rules declarative; do not modify `packages/workflow_engine`.
- Preserve existing source/workflow/dataset IDs and revisions.
- Enable and reconcile only schedules owned by the Belarus market package; do not mutate separately named operator schedules.
- Use `0 * * * *` and `Europe/Minsk` exactly.
- Keep API authentication and the existing `from`/`to` data API semantics unchanged.

---

### Task 1: Hourly package schedule bootstrap

**Files:**
- Modify: `tests/unit/test_belarus_market_pack.py: test_imported_verified_schedule_starts_disabled, test_pack_installer_is_idempotent_and_creates_per_source_workflows`
- Modify: `apps/api/app/services/belarus_market_pack.py: _schedule_defaults, install_belarus_market_pack`

**Interfaces:**
- Consumes: `passport_sources() -> list[PassportSource]` and `install_belarus_market_pack(db, admin) -> dict[str, int]`.
- Produces: every package `Schedule` has `cron == "0 * * * *"`, `timezone == "Europe/Minsk"`, and `enabled is True`; a separately named schedule remains unchanged.

- [ ] **Step 1: Write the failing schedule tests**

```python
def test_imported_verified_schedule_starts_enabled_hourly(client):
    with SessionLocal() as db:
        schedule = db.scalar(select(Schedule).join(Workflow).where(Workflow.name.like("ul-20:%")))
    assert schedule is not None
    assert (schedule.cron, schedule.timezone, schedule.enabled) == ("0 * * * *", "Europe/Minsk", True)


def test_reimport_reconciles_pack_schedule_but_preserves_operator_schedule(client):
    with SessionLocal() as db:
        admin = db.scalar(select(User).order_by(User.created_at))
        package_schedule = db.scalar(select(Schedule).join(Workflow).where(Workflow.name.like("new-news-01:%")))
        operator_schedule = Schedule(
            workflow_id=package_schedule.workflow_id,
            name="Операторский ночной запуск",
            cron="30 23 * * *",
            timezone="UTC",
            enabled=False,
        )
        package_schedule.cron, package_schedule.enabled = "0 8 * * 1-5", False
        db.add(operator_schedule); db.commit()
        install_belarus_market_pack(db, admin)
        db.refresh(package_schedule); db.refresh(operator_schedule)
    assert (package_schedule.cron, package_schedule.timezone, package_schedule.enabled) == ("0 * * * *", "Europe/Minsk", True)
    assert (operator_schedule.cron, operator_schedule.timezone, operator_schedule.enabled) == ("30 23 * * *", "UTC", False)
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest -q tests/unit/test_belarus_market_pack.py -k 'starts_enabled_hourly or reconciles_pack_schedule'`

Expected: FAIL because imported schedules are disabled and have source-specific legacy cron values.

- [ ] **Step 3: Implement the minimal installer change**

```python
def _schedule_defaults(source: PassportSource) -> tuple[str, str]:
    return ("0 * * * *", "Europe/Minsk")

# After locating the package schedule by its canonical or legacy package name:
schedule.cron, schedule.timezone, schedule.enabled = (*_schedule_defaults(descriptor), True)
```

Perform this assignment only for the schedule found through `schedule_name` or
`legacy_workflow_name`; never query or update arbitrary schedules attached to
the same workflow.

- [ ] **Step 4: Run focused tests to verify they pass**

Run: `pytest -q tests/unit/test_belarus_market_pack.py -k 'starts_enabled_hourly or reconciles_pack_schedule or pack_installer_is_idempotent'`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/app/services/belarus_market_pack.py tests/unit/test_belarus_market_pack.py
git commit -m "feat: enable hourly market pack schedules"
```

### Task 2: Zero-touch startup regression coverage

**Files:**
- Modify: `tests/unit/test_belarus_market_pack.py: test_pack_installer_is_idempotent_and_creates_per_source_workflows`
- Modify: `apps/api/app/bootstrap.py: seed` only if its existing `install_belarus_market_pack(db, admin)` call cannot satisfy the test.

**Interfaces:**
- Consumes: FastAPI test client startup, which executes `app.main.lifespan` and `app.bootstrap.seed`.
- Produces: a clean startup exposes `market-news`, all 15 website news memberships and enabled hourly package schedules without an API/UI configuration call.

- [ ] **Step 1: Extend the Task 1 failing test with a clean-start assertion**

```python
def test_pack_installer_is_idempotent_and_creates_per_source_workflows(client):
    with SessionLocal() as db:
        news = db.scalar(select(Dataset).where(Dataset.slug == "market-news"))
        news_memberships = db.scalars(
            select(DatasetSourceMembership).where(DatasetSourceMembership.dataset_id == news.id)
        ).all()
        schedules = db.scalars(select(Schedule).join(Workflow).where(Workflow.project_id == news.project_id)).all()
    assert {row.source_key for row in news_memberships if row.source_key != "news-03"} == {
        "news-01", "news-02", "news-04", "news-05", "news-06", "news-07", "news-08",
        "news-09", "news-10", "news-11", "news-12", "news-13", "news-14", "news-15", "news-16",
    }
    assert all(item.enabled and item.cron == "0 * * * *" for item in schedules)
```

- [ ] **Step 2: Run the assertion together with the Task 1 RED test**

Run: `pytest -q tests/unit/test_belarus_market_pack.py::test_pack_installer_is_idempotent_and_creates_per_source_workflows`

Expected: FAIL because the pre-change bootstrap creates disabled, non-hourly schedules.

- [ ] **Step 3: Keep bootstrap minimal after the Task 1 production change**

```python
def seed(db: Session) -> None:
    # Existing idempotent call remains the single startup provisioner.
    install_belarus_market_pack(db, admin)
```

Do not add a second importer, a request-time bootstrap, or a synchronous
network crawl to FastAPI lifespan.

- [ ] **Step 4: Run the bootstrap and pack tests**

Run: `pytest -q tests/unit/test_belarus_market_pack.py`

Expected: PASS.

- [ ] **Step 5: Commit the bootstrap characterization if it was not committed with Task 1**

```bash
git add apps/api/app/bootstrap.py tests/unit/test_belarus_market_pack.py
git commit -m "test: prove market digest bootstraps without setup"
```

### Task 3: Destiny deployment and date-filter API guide

**Files:**
- Modify: `.env.example`
- Modify: `README.md: Quick start and Belarus market section`
- Modify: `docs/belarus_market/OPERATIONS.md`
- Create: `docs/belarus_market/DESTINY_API_GUIDE.md`
- Test: `tests/integration/test_data_api_contract.py: test_source_published_at_range`

**Interfaces:**
- Consumes: `GET /api/v1/datasets/{dataset_ref}/records` with a scoped Bearer token and parameters `view`, `time_basis`, `from`, `to`, `sort`, `limit`, `cursor`.
- Produces: copyable `https://destiny.by` request examples for a specific date using `market-news`; docs state that all timestamps must be timezone-aware and `+03:00` is URL encoded as `%2B`.

- [ ] **Step 1: Write a failing `market-news` API date-filter test**

```python
def test_market_news_source_publication_range_is_supported(client, auth):
    dataset, _, _, _ = create_observed_dataset(
        client, auth, source_published_at="2026-08-17T05:15:00+03:00"
    )
    dataset["slug"] = "market-news"
    response = client.get(
        "/api/v1/datasets/market-news/records?view=current"
        "&time_basis=source_published_at"
        "&from=2026-08-17T00:00:00%2B03:00"
        "&to=2026-08-18T00:00:00%2B03:00&sort=asc",
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["items"]
```

Adapt the existing test fixture rather than changing the Data API contract;
create the dataset with slug `market-news` through its helper if the fixture
cannot safely rename a persisted row.

- [ ] **Step 2: Run the targeted date-filter test**

Run: `pytest -q tests/integration/test_data_api_contract.py -k market_news_source_publication_range`

Expected: PASS if the existing API contract already supplies the behavior; the
test is a characterization guard, not a router change.

- [ ] **Step 3: Add deployment and API documentation**

```dotenv
PUBLIC_APP_URL=https://destiny.by
CORS_ORIGINS=https://destiny.by
```

```bash
curl --get 'https://destiny.by/api/v1/datasets/market-news/records' \
  --header "Authorization: Bearer $DESTINY_API_TOKEN" \
  --data-urlencode 'view=current' \
  --data-urlencode 'time_basis=source_published_at' \
  --data-urlencode 'from=2026-08-17T00:00:00+03:00' \
  --data-urlencode 'to=2026-08-18T00:00:00+03:00' \
  --data-urlencode 'sort=asc' \
  --data-urlencode 'limit=100'
```

Explain that `from` is inclusive, `to` exclusive, `cursor` is copied from
`pagination.next_cursor`, and date filtering excludes records whose original
source publication timestamp is unknown.

- [ ] **Step 4: Verify docs and API test**

Run: `pytest -q tests/integration/test_data_api_contract.py && rg -n 'destiny\.by|market-news|source_published_at|0 \* \* \* \*' README.md .env.example docs/belarus_market`

Expected: PASS and every required deployment/API phrase is present.

- [ ] **Step 5: Commit**

```bash
git add .env.example README.md docs/belarus_market/OPERATIONS.md docs/belarus_market/DESTINY_API_GUIDE.md tests/integration/test_data_api_contract.py
git commit -m "docs: add destiny market news API guide"
```

## Final verification

- [ ] Run: `pytest -q --ignore=tests/unit/test_transport_policy.py`
- [ ] Run: `ruff check apps/api/app/services/belarus_market_pack.py tests/unit/test_belarus_market_pack.py tests/integration/test_data_api_contract.py`
- [ ] Run: `git diff --check && git status --short`
- [ ] Record the known independent `test_fetch_policy_rejects_unbounded_values` failure only if it still reproduces unchanged on `main`; do not alter the generic engine in this feature.
