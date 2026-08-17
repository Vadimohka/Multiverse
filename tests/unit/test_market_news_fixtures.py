import sys
from pathlib import Path

import pytest


FIXTURE_HELPERS = Path(__file__).resolve().parents[1] / "fixtures" / "belarus-market"
sys.path.insert(0, str(FIXTURE_HELPERS))

from news import run_news_fixture


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
