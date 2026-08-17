import sys
from datetime import datetime
from html import escape
from json import loads
from pathlib import Path
from urllib.parse import urljoin

import pytest
from app.database import SessionLocal
from app.models import DatasetSourceMembership, Workflow
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from sqlalchemy import select
from workflow_engine.nodes import (
    ExtractRepeatingListNode,
    TransformNode,
    build_url_frontier,
)
from workflow_engine.types import ExecutionContext

FIXTURE_HELPERS = Path(__file__).resolve().parents[1] / "fixtures" / "belarus-market"
sys.path.insert(0, str(FIXTURE_HELPERS))

from news import (  # noqa: E402
    _fixture_config,
    run_news_fixture,
    run_news_fixture_from_files,
)

DETAIL_URL = "https://www.centraldepo.by/news/public-market-update/"
LISTING = f"""
<div class="news-list__item">
  <div class="item-title"><a href="{DETAIL_URL}">Обновление рынка</a></div>
</div>
"""
DETAIL = """
<html><head>
  <meta property="article:published_time" content="2026-08-14T09:00:00+03:00">
</head><body>
  <h1>Обновление рынка</h1>
  <article>Полный публичный текст</article>
</body></html>
"""
WINDOW = {"from": "2026-08-14T00:00:00+03:00", "to": "2026-08-15T00:00:00+03:00"}
VERIFICATION = loads(
    (Path(__file__).resolve().parents[2] / "presets" / "belarus-market" / "verification.json").read_text(
        encoding="utf-8"
    )
)


def _fixture_detail(title: str, body: str) -> str:
    """Render a sanitized detail response from public listing metadata."""
    return f"""
    <!doctype html><html><head>
      <meta property="article:published_time" content="2026-08-14T09:00:00+03:00">
    </head><body>
      <h1>{escape(title)}</h1>
      <article>{escape(body)}</article>
    </body></html>
    """


async def _run_sanitized_listing(
    source_key: str,
    *fixture_names: str,
):
    """Run a compiled profile over retained public listing structures."""
    source, config = _fixture_config(source_key, WINDOW)
    selector = config["nodes"]["traverse"]["detail"]["selector"]
    fixture_bodies = [
        (FIXTURE_HELPERS / "news" / fixture_name).read_text(encoding="utf-8")
        for fixture_name in fixture_names
    ]
    details = {}
    for fixture_body in fixture_bodies:
        for link in BeautifulSoup(fixture_body, "lxml").select(selector):
            url = urljoin(source.url, str(link.get("href") or ""))
            title = link.get_text(" ", strip=True)
            details[url] = _fixture_detail(
                title,
                str(link.get("data-public-summary") or title),
            )
    pages = {}
    pagination = config["nodes"]["traverse"].get("pagination") or {}
    template = str(pagination.get("urlTemplate") or "")
    for page_number, fixture_body in enumerate(fixture_bodies[1:], start=2):
        pages[template.replace("{{page}}", str(page_number))] = fixture_body
    return await run_news_fixture(
        source_key,
        fixture_bodies[0],
        details,
        {**WINDOW, "max_pages": len(fixture_bodies)},
        pages=pages,
    )


def test_unavailable_nbrb_statistics_html_is_not_accepted_fixture_evidence():
    evidence = VERIFICATION["news-05"]
    detail = BeautifulSoup(
        (FIXTURE_HELPERS / "news" / "news-05-detail.html").read_text(encoding="utf-8"),
        "lxml",
    )

    assert evidence["status"] == "DRAFT"
    assert "fixture_refs" not in evidence
    assert evidence["live_smoke"]["result"] == "PENDING_OPERATOR_SMOKE"
    assert "read_timeout" in evidence["live_smoke"]["reason"]
    assert detail.select_one("meta[property='article:published_time']") is None
    assert detail.select_one("[data-evidence-status='unavailable']")


def test_economy_fixture_proves_only_observed_direct_pdf_link_discovery():
    _source, config = _fixture_config("news-06", WINDOW)
    traverse = config["nodes"]["traverse"]
    listing = (FIXTURE_HELPERS / "news" / "news-06-list.html").read_text(
        encoding="utf-8"
    )
    soup = BeautifulSoup(listing, "lxml")
    items = [
        {"url": link["href"], "title": link.get_text(" ", strip=True)}
        for link in soup.select(traverse["detail"]["selector"])
    ]

    frontier = build_url_frontier(
        items,
        base_url="https://economy.gov.by/ru/aktualnaya-informatsiya-ru/",
        origin_url="https://economy.gov.by/ru/aktualnaya-informatsiya-ru/",
        url_path="url",
        config=traverse,
        limit=10,
    )

    assert [candidate["url"] for candidate in frontier] == [
        "https://economy.gov.by/uploads/files/macro-aktual-ifo/2026-03-itog.pdf"
    ]
    assert VERIFICATION["news-06"]["fixture_refs"] == [
        "tests/fixtures/belarus-market/news/news-06-list.html"
    ]
    assert "attachment_url" not in {
        field["name"] for field in traverse["detail"]["fields"]
    }


def test_nbrb_uppercase_press_route_passes_compiled_generic_frontier_pattern():
    _source, config = _fixture_config("news-04", WINDOW)
    traverse = config["nodes"]["traverse"]
    evidence = VERIFICATION["news-04"]
    detail = BeautifulSoup(
        (FIXTURE_HELPERS / "news" / "news-04-detail.html").read_text(encoding="utf-8"),
        "lxml",
    )

    frontier = build_url_frontier(
        [{"url": "https://www.nbrb.by/Press/22322"}],
        base_url="https://www.nbrb.by/",
        origin_url="https://www.nbrb.by/news/press",
        url_path="url",
        config=traverse,
        limit=1,
    )

    assert [candidate["url"] for candidate in frontier] == [
        "https://www.nbrb.by/Press/22322"
    ]
    assert evidence["status"] == "DRAFT"
    assert "fixture_refs" not in evidence
    assert detail.select_one("meta[property='article:published_time']") is None
    assert detail.select_one("[data-evidence-status='unavailable']")


@pytest.mark.parametrize(
    "source_key", ["news-01", "news-02", "news-04", "news-05", "news-06", "news-07", "news-08"]
)
@pytest.mark.asyncio
async def test_official_source_has_fixture_evidence_or_explicit_draft_reason(source_key):
    """Every official source needs accepted evidence or a concrete DRAFT reason."""
    if source_key in {"news-04", "news-05"}:
        evidence = VERIFICATION[source_key]
        assert evidence["status"] == "DRAFT"
        assert "fixture_refs" not in evidence
        assert evidence["live_smoke"]["result"] == "PENDING_OPERATOR_SMOKE"
        assert "read_timeout" in evidence["live_smoke"]["reason"]
        return
    if source_key == "news-06":
        assert VERIFICATION[source_key]["fixture_refs"] == [
            "tests/fixtures/belarus-market/news/news-06-list.html"
        ]
        assert "PDF_MIME_and_parser_behavior_not_fixture_tested" in VERIFICATION[source_key][
            "live_smoke"
        ]["reason"]
        return

    result = await run_news_fixture_from_files(source_key)

    assert result.records
    assert all(record["title"] and record["canonical_url"] for record in result.records)
    assert all(record["source_published_at"] and record["body_text"] for record in result.records)
    assert all(
        record["selection_rule_id"] and record["selection_reason"]
        for record in result.records
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("source_key", ["news-01", "news-02"])
async def test_retained_fixture_executes_the_actual_installed_special_graph(
    client, source_key
):
    """Fixture evidence must fail when an installed official graph drifts."""

    with SessionLocal() as db:
        membership = db.scalar(
            select(DatasetSourceMembership).where(
                DatasetSourceMembership.source_key == source_key
            )
        )
        assert membership is not None
        workflow = db.get(Workflow, membership.workflow_id)
        assert workflow is not None
        installed_graph = workflow.graph_json

    result = await run_news_fixture_from_files(source_key, graph=installed_graph)

    assert result.records
    assert all(record["body_text"] for record in result.records)
    assert all(record["selection_rule_id"] for record in result.records)


@pytest.mark.asyncio
@pytest.mark.filterwarnings(
    f"ignore::{XMLParsedAsHTMLWarning.__module__}.{XMLParsedAsHTMLWarning.__name__}"
)
async def test_official_fixture_source_specific_contracts():
    """Format-specific evidence must remain connected to generic execution."""
    _source, news_05_config = _fixture_config("news-05", WINDOW)
    selection_operation = next(
        operation
        for operation in news_05_config["nodes"]["process"]["operations"]
        if operation["type"] == "select_by_rules"
    )
    news_05 = await TransformNode().execute(
        ExecutionContext(
            run_id="selection-fixture",
            project_id="belarus-market",
            workflow_version_id="fixture-v2",
        ),
        {
            "records": [
                {
                    "title": (
                        "Сведения о средних процентных ставках "
                        "кредитно-депозитного рынка Республики Беларусь"
                    )
                }
            ]
        },
        {"operations": [selection_operation]},
    )
    news_08 = await run_news_fixture_from_files("news-08")

    assert news_05["records"][0]["selection_rule_id"] in {
        "nbrb-statistics-credit-deposit-v2",
        "nbrb-statistics-corporate-securities-v1",
    }
    assert news_08.pagination.visited_pages == 2
    assert news_08.traversal["reconciliation"] == {
        "discovered": 2,
        "succeeded": 2,
        "intentionally_skipped": 0,
        "failed": 0,
        "duplicate": 0,
    }
    assert news_08.records[0]["body_text"] != news_08.records[0]["title"]

    page_dates = []
    for fixture_name in ("news-08-page-1.html", "news-08-page-2.html"):
        soup = BeautifulSoup((FIXTURE_HELPERS / "news" / fixture_name).read_text(encoding="utf-8"), "lxml")
        page_dates.append(datetime.strptime(soup.select_one(".date").get_text(strip=True), "%d.%m.%Y"))
    assert page_dates == sorted(page_dates, reverse=True)

    feed = (FIXTURE_HELPERS / "news" / "news-04-feed.xml").read_text(encoding="utf-8")
    rss = await ExtractRepeatingListNode().execute(
        ExecutionContext(run_id="rss-fixture", project_id="belarus-market", workflow_version_id="fixture-v2"),
        {"body": feed},
        {
            "container_selector": "item",
            "fields": [
                {"name": "title", "selector": "title"},
                {"name": "url", "selector": "link"},
            ],
        },
    )
    assert rss["records"][0]["url"] == "https://www.nbrb.by/Press/22322"


@pytest.mark.asyncio
async def test_fixture_runner_keeps_detail_date_and_provenance():
    """A missing generic list/detail pipeline would drop public source evidence."""
    result = await run_news_fixture("news-08", LISTING, {DETAIL_URL: DETAIL}, WINDOW)

    assert result.records[0]["body_text"] == "Полный публичный текст"
    assert result.records[0]["source_published_at"] == "2026-08-14T09:00:00+03:00"
    assert result.records[0]["__provenance"]["state"] == "detail:1"
    assert result.assessment_status == "PASS"


@pytest.mark.asyncio
async def test_fixture_runner_marks_an_empty_checked_window_as_valid():
    """Dropping allow-empty handling would turn a valid quiet window into a failure."""
    valid_empty = await run_news_fixture("news-08", "<main></main>", {}, WINDOW)

    assert valid_empty.assessment_codes == ["EMPTY_VALID_WINDOW"]
    assert valid_empty.assessment_status == "PASS"


@pytest.mark.asyncio
async def test_fixture_runner_reports_partial_detail_failure():
    """Ignoring a failed detail fetch would hide incomplete collection evidence."""
    missing_url = "https://www.centraldepo.by/news/missing-update/"
    listing = LISTING + f"""
    <div class="news-list__item"><div class="item-title">
      <a href="{missing_url}">Недоступное обновление</a>
    </div></div>
    """

    partial = await run_news_fixture("news-08", listing, {DETAIL_URL: DETAIL}, WINDOW)

    assert partial.assessment_status == "PARTIAL"
    assert "DETAIL_FAILURE" in partial.assessment_codes


@pytest.mark.asyncio
async def test_fixture_runner_reports_repeated_pagination_page():
    """Removing generic frontier loop protection would allow a repeated page."""
    listing = """
    <section id="newsData"><a rel="next" href="https://www.nbrb.by/news/statistics">Следующая</a></section>
    """

    repeated_page = await run_news_fixture(
        "news-05", listing, {}, {**WINDOW, "max_pages": 2}
    )

    assert repeated_page.traversal["reconciliation"]["failed"] == 0
    assert "REPEATED_PAGE" in repeated_page.assessment_codes


@pytest.mark.asyncio
async def test_paid_article_keeps_only_public_metadata():
    """Removing access redaction would expose a commercial article body."""
    records = (await run_news_fixture_from_files("news-09")).records
    paid = next(record for record in records if record["access_status"] == "PAYWALLED")
    public = next(record for record in records if record["access_status"] == "PUBLIC")

    assert public["title"] == "Public market analysis"
    assert public["body_text"] == "Public market analysis excerpt."
    assert paid["title"] == "Commercial sector outlook"
    assert paid["canonical_url"]
    assert paid["source_published_at"] == "2026-08-14T10:00:00+03:00"
    assert paid["summary_raw"] == "Subscription required; public metadata only."
    assert paid["body_text"] is None
    assert paid["body_html"] is None
    assert paid["access_evidence"]["matched_terms"] == ["subscription required"]


@pytest.mark.asyncio
async def test_finance_scope_keeps_decisions_explainable():
    """Changing rule order or defaults would silently change finance scope."""
    records = (await _run_sanitized_listing("news-10", "news-10-list.html")).records
    finance_by_title = {record["title"]: record for record in records}

    assert {record["candidate_status"] for record in records} == {
        "INCLUDE",
        "EXCLUDE",
        "AMBIGUOUS",
    }
    assert finance_by_title["Bank deposit promotion"]["candidate_status"] == "EXCLUDE"
    assert finance_by_title["Government bonds market"]["candidate_status"] == "INCLUDE"
    assert finance_by_title["Household finance outlook"]["candidate_status"] == "AMBIGUOUS"
    assert all(record["selection_rule_id"] for record in records)
    assert all(record["selection_reason"] for record in records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_key", "fixture_name", "expected_title"),
    [
        ("news-11", "news-11-list.html", "Securities category report"),
        ("news-12", "news-12-list.html", "Precious metals category report"),
        ("news-13", "news-13-list.html", "Analysis category report"),
    ],
)
async def test_myfin_scopes_exclude_global_recommendations(
    source_key, fixture_name, expected_title
):
    """Broadening a scoped selector would leak an unrelated global card."""
    records = (await _run_sanitized_listing(source_key, fixture_name)).records

    assert [record["title"] for record in records] == [expected_title]
    assert records[0]["candidate_status"] == "INCLUDE"


@pytest.mark.asyncio
async def test_precious_metal_topics_apply_include_exclude_and_paid_contracts():
    """A lost topic or access rule would admit noise or expose paid text."""
    phoenix = await _run_sanitized_listing("news-14", "news-14-list.html")
    phoenix_by_title = {record["title"]: record for record in phoenix.records}
    business_times = await _run_sanitized_listing("news-15", "news-15-list.html")
    business_by_title = {record["title"]: record for record in business_times.records}

    assert phoenix_by_title["Gold recycling outlook"]["candidate_status"] == "INCLUDE"
    assert phoenix_by_title["Copper recycling outlook"]["candidate_status"] == "EXCLUDE"
    business_times_paid = business_by_title["Gold market - subscription required"]
    assert business_times_paid["candidate_status"] == "INCLUDE"
    assert business_times_paid["access_status"] == "PAYWALLED"
    assert business_times_paid["body_text"] is None
    assert business_by_title["Silver market outlook"]["candidate_status"] == "AMBIGUOUS"


@pytest.mark.asyncio
async def test_texmetals_non_repeating_pages_keep_unique_article_identities():
    """Losing page/frontier dedupe would emit one article observation twice."""
    texmetals = await _run_sanitized_listing(
        "news-16", "news-16-page-1.html", "news-16-page-2.html"
    )

    assert texmetals.pagination.visited_pages == 2
    assert texmetals.traversal["stop_reason"] == "MAX_PAGES"
    assert len(texmetals.records) == 3
    assert len(texmetals.records) == len(
        {record["identity_key"] for record in texmetals.records}
    )


@pytest.mark.parametrize(
    ("source_key", "fixture_refs"),
    [
        (
            "news-09",
            [
                "tests/fixtures/belarus-market/news/news-09-list.html",
                "tests/fixtures/belarus-market/news/news-09-detail.html",
                (
                    "tests/fixtures/belarus-market/news/"
                    "news-09-detail-commercial-sector-outlook-1002.html"
                ),
            ],
        ),
        ("news-10", ["tests/fixtures/belarus-market/news/news-10-list.html"]),
        ("news-11", ["tests/fixtures/belarus-market/news/news-11-list.html"]),
        ("news-12", ["tests/fixtures/belarus-market/news/news-12-list.html"]),
        ("news-13", ["tests/fixtures/belarus-market/news/news-13-list.html"]),
        ("news-14", ["tests/fixtures/belarus-market/news/news-14-list.html"]),
        ("news-15", ["tests/fixtures/belarus-market/news/news-15-list.html"]),
        (
            "news-16",
            [
                "tests/fixtures/belarus-market/news/news-16-page-1.html",
                "tests/fixtures/belarus-market/news/news-16-page-2.html",
            ],
        ),
    ],
)
def test_commercial_fixture_references_remain_draft_after_anonymous_live_check(
    source_key, fixture_refs
):
    """Fixture evidence must not silently promote a source or claim an operator run."""
    evidence = VERIFICATION[source_key]

    assert evidence["status"] == "DRAFT"
    assert evidence["fixture_refs"] == fixture_refs
    assert evidence["live_smoke"]["result"] == "PENDING_OPERATOR_SMOKE"
