import sys
from datetime import datetime
from json import loads
from pathlib import Path

import pytest
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from workflow_engine.nodes import (
    ExtractRepeatingListNode,
    TransformNode,
    build_url_frontier,
)
from workflow_engine.types import ExecutionContext


FIXTURE_HELPERS = Path(__file__).resolve().parents[1] / "fixtures" / "belarus-market"
sys.path.insert(0, str(FIXTURE_HELPERS))

from news import _fixture_config, run_news_fixture, run_news_fixture_from_files


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
